# 01 — System, Functional & Deployment Architecture

## 1. System architecture

```
                         ┌────────────────────── PRINCIPAL (you) ───────────────────────┐
                         │ browser / PWA (desktop, Chromebook, phone) · CLI · MCP client │
                         └──────────────┬───────────────────────────────┬───────────────┘
                                        │ HTTPS over Tailscale          │ stdio
┌───────────────────────────── apps/web (Next.js, Node) ────────────┐ ┌─┴──────────────┐
│ UI (RSC) · REST /api/v1 · SSE /api/v1/stream · auth (passkeys)    │ │ apps/cli (mcp) │
│ reads: query services + cached projections   writes: commands ──┐ │ └────────────────┘
└─────────────────────────────────────────────────────────────────┼─┘
                                                                  ▼
┌────────────────────────── packages/* : the modular monolith core ─────────────────────────┐
│ EventService ─► outbox ─► EventBus ─► handlers (projections, triggers, policies)          │
│ MetricService · AnalyticsService · GoalService · ExperimentService · ForecastService      │
│ WeaknessEngine · BottleneckEngine · RecommendationService · NotificationService          │
│ MemoryService · KnowledgeGraph · PolicyService · ApprovalService · VerificationService   │
│ AgentService (Orchestrator, Agent Factory, Registry) · ToolService · SkillService        │
│ ModelRouter → LLM providers   IntegrationService → adapters                               │
└──────────────────────────────────────┬────────────────────────────────────────────────────┘
                                       │ same packages, other process
┌──────────────────────────── apps/worker (Node) ───────────────────────────────────────────┐
│ Scheduler (cron) · JobQueue consumer (leases) · Agent runtime (runs, checkpoints)        │
│ Integration syncs · heavy analytics recompute · tool sandbox supervisor                  │
└──────────────────────────────────────┬────────────────────────────────────────────────────┘
                                       ▼
          SQLite (WAL) apex.db  ·  sqlite-vec index  ·  encrypted blobs dir  ·  backups (age)
                                       │ (only what a call needs)
                    Anthropic API (primary) · other LLM providers · web · job sources · IMAP/ICS …
```

### ADR-01 Modular monolith

- **DECISION.** One codebase, two processes: `web` (UI, API, commands, cheap reads) and `worker` (everything slow, scheduled or agentic). They share the domain packages and the database.
- **RATIONALE.** One user and one machine. Module boundaries give the needed separation without network hops, distributed transactions or ops load. The agent runtime is isolated from the UI as the brief requires, so a crashing or long run never blocks the dashboard.
- **ALTERNATIVES.**
  - Microservices: rejected (operational cost, distributed failure modes).
  - A single process: rejected, because agent runs and heavy analytics would compete with UI latency.
- **TRADE-OFFS.** Two processes share one SQLite file, so writes must be short transactions (WAL makes this fine at personal scale). Module boundaries are enforced by lint rules (`eslint-plugin-boundaries`), not by the network.

### ADR-02 TypeScript end to end, v0.3 as reference implementation

- **DECISION.** Build v1.0 in TypeScript: Next.js (App Router) for web and Node for the worker, with shared `packages/*`. Port v0.3 (Python) module by module. Each ported module must pass **golden tests** generated from v0.3 on the same fixtures (formulas, policies, agents). v0.3 stays the system of record until each module reaches parity, so every phase leaves a working system.
- **RATIONALE.**
  - It is your stated preference.
  - One language means shared types from DB to UI (zod contracts), one toolchain and one test runner, which suits years of evolution with Codex and Claude Code.
  - The v1.0 data model (raw events first, metric registry, KG) changes the core anyway: most code is new design, not translation.
  - The statistics APEX needs (means, robust trends, Welch t, Pearson/Spearman, bootstrap CIs, Beta posteriors, half-life regression, simple growth models) are well within TS (`simple-statistics` plus small in-house code).
- **ALTERNATIVES.**
  - (a) Keep the Python core and add a Next.js UI over the existing REST API. Least rework, but two languages forever, duplicated types and the v0.3 schema carried forward.
  - (b) Python core plus a TS UI, with a Python worker only for statistics. A reasonable hedge, but it brings back cross-language contracts.
- **TRADE-OFFS.** A rebuild costs calendar time (≈ phases 0–2 before full parity). It is mitigated by porting in phase order and keeping v0.3 running. If advanced modelling is ever needed (hierarchical Bayesian forecasting), an *optional* Python analytics sidecar can be added behind `AnalyticsService` without touching callers.

### Cross-cutting runtime properties

| Property | Mechanism |
|---|---|
| Fast dashboard | Reads go to **projections** (materialized tables updated by event handlers) plus an in-memory LRU. Never call an LLM during render. Target p95 < 300 ms for Mission Control. |
| Heavy work off the request path | Commands enqueue jobs; the worker executes them; the UI subscribes over SSE |
| Determinism | Numbers come from deterministic code with versioned formulas; LLMs interpret, plan and draft |
| Idempotency | Every command and event carries an id; handlers keep a processed ledger |
| Explainability | Every recommendation, score and agent action links to its evidence (event ids, metric values, sources) |

## 2. Functional architecture

```
                 ┌──────────── STRATEGY ────────────┐
 Goals hierarchy │ Life direction → … → Next action │◄─ Decision Journal, Forecasts
                 └───────────────┬──────────────────┘
                                 │ alignment, risk, drift
 ┌──────── SENSE ───────┐  ┌─────▼──── UNDERSTAND ─────────┐  ┌──────── ACT ─────────────┐
 │ manual quick capture │  │ Metric registry & analytics   │  │ Priority Engine (Top 3)  │
 │ integrations         │─►│ Weakness / Bottleneck engines │─►│ Orchestrator → agents    │
 │ agent observations   │  │ Learning intelligence         │  │ Policy → Approval        │
 │ raw events           │  │ Digital twin / KG / memory    │  │ Execution & verification │
 └──────────────────────┘  └───────────────────────────────┘  └───────────┬──────────────┘
           ▲                            LEARN: experiments, reflection,   │
           └─────────────────────────── organizational learning ◄─────────┘
```

### Domains (pluggable)

Every domain is a **DomainModule** (doc 06 §DomainModule): event types, metric definitions, weakness
taxonomy, a command-center view, an analyst agent manifest and forecast models. Adding a domain means
adding a package and registering it; the core never changes.

| Domain | Primary metrics (definitions in doc 03) | Specialist |
|---|---|---|
| German | CEFR evidence per skill, active share, error recurrence, retention curve, WPM, verified learning/h | German Agent |
| Law | mastery per area (Beta posterior), error-type mix, readiness, time/question, retention | Law Agent |
| Learning | retention 1/7/30/90/180 d, retained learning/h, transfer | Learning Scientist |
| Productivity / Execution | completion, priority completion, plan accuracy, carry-over, latency, start delay | Productivity Agent |
| Deep Work | DWQ score, uninterrupted minutes, interruptions/h | Productivity Agent |
| Attention / Digital hygiene | phone min vs target, pickups, intentional share, switches | Attention Agent |
| Recovery / Sustainability | sustainability index, sleep regularity, energy trend | Recovery Agent |
| Goals | trajectory vs plan, drift, neglect, conflicts | Orchestrator |
| Career | evidence-based match, pipeline funnel, skill-gap closure | Career Agent, Opportunity Scout |
| Knowledge | notes/resources linked to skills, review queue | Knowledge Agent |
| Projects | milestone burn-down, stall detection | Orchestrator |
| Opportunities | opportunity score, decay | Opportunity Scout |
| Experiments | active tests, evidence level | Experiment Agent |
| Decisions | calibration (expected vs actual) | Critic Agent |

### Minimal manual input (< 2 min/day)

- **One-tap quick capture.** A PWA widget handles energy, a session done ✓ and phone minutes.
- **Automatic sources first.** Calendar, screen-time export, health export, flashcard app export, exam platform CSV and email headers.
- **Inference with confirmation.** Agents propose logs ("You studied 45 min of Law at 09:00?"), and one tap confirms them.
- **Cost guard.** The system measures its own input cost (seconds of manual input per day, from UI timings) as a tracked metric.

## 17. Repository structure (ADR-03)

```
apex/                                  pnpm workspaces · Turborepo task graph · Node 22 LTS
├─ apps/
│  ├─ web/            Next.js App Router: UI, route handlers (/api/v1), SSE, auth
│  ├─ worker/         scheduler, job consumer, agent runtime, sandbox supervisor
│  └─ cli/            admin CLI + MCP server (stdio)
├─ packages/
│  ├─ contracts/      zod schemas → TS types + OpenAPI (single source of truth for I/O)
│  ├─ db/             Drizzle schema, migrations, repositories, test DB factory
│  ├─ events/         event type registry, outbox, bus, idempotency ledger, DLQ
│  ├─ metrics/        metric registry, formulas (pure), recompute DAG
│  ├─ analytics/      stats lib, correlations, cohorts, analysis DSL executor
│  ├─ domains/        german/, law/, learning/, execution/, attention/, recovery/, career/, …
│  ├─ engines/        weakness/, bottleneck/, priority/, experiments/, forecast/, sustainability/
│  ├─ goals/          hierarchy, alignment, drift detection
│  ├─ memory/         memory service, consolidation, retrieval (FTS + vectors)
│  ├─ kg/             knowledge graph store + query API
│  ├─ policy/         constitution, autonomy grants, risk scoring, approval engine
│  ├─ verification/   verifiers, verification records
│  ├─ llm/            provider abstraction, Model Router, cost meter, prompt registry
│  ├─ agents/         runtime loop, Orchestrator, Agent Factory, registry, fitness, teams
│  ├─ tools/          tool registry, built-in tools, Tool Factory, sandbox client
│  ├─ skills/         skill registry, workflow engine, Skill Factory
│  ├─ integrations/   adapter interfaces + adapters (ics, imap, health, jobs, rss, push, mcp)
│  ├─ notifications/  inbox, interruption-value model, delivery
│  ├─ security/       auth, crypto (libsodium), secrets, audit
│  ├─ ui/             design system (tokens, primitives, charts)
│  └─ testkit/        fixtures, fake LLM (record/replay), simulators, golden data from v0.3
├─ legacy/v03/        the current Python system (reference + golden-test generator) until retired
├─ docs/              spec (this), ADRs, runbooks
└─ infra/             docker-compose, Dockerfiles, backup scripts, Tailscale notes
```

Dependency rule (lint-enforced): `apps → packages`. Inside `packages` dependencies point one way only:
**contracts ← db ← events ← (metrics, memory, kg, policy, llm) ← (domains, engines, goals, verification) ← (tools, skills) ← agents**.
`ui` depends only on `contracts`.

## 21. Deployment architecture (ADR-04)

- **DECISION.**
  - **Local-first, single host:** `docker compose` with `web`, `worker` and a data volume (`apex.db`, blobs, keys, backups).
  - The host is the user's always-on machine (mini PC or NAS) **or** a small private VPS.
  - Access goes **only** over Tailscale (WireGuard mesh, device auth, MagicDNS, HTTPS certs); there is no public port.
  - Phone and Chromebook use the PWA.
- **RATIONALE.**
  - Your raw personal data stays on hardware you control.
  - The private network removes most of the internet attack surface (no public login page).
  - It is always reachable from your devices.
- **ALTERNATIVES.**
  - Public VPS with a reverse proxy and WAF: workable, but a larger attack surface.
  - Managed PaaS: data leaves your control.
  - Desktop-only app: no phone access.
- **TRADE-OFFS.**
  - The home host needs uptime and its own backups; mitigated by encrypted off-site backups (doc 05).
  - A VPS means the provider has physical access; mitigated by field encryption, with keys optionally kept off-host.

```yaml
# infra/docker-compose.yml (shape)
services:
  web:    { image: apex-web,    env_file: .env, volumes: [data:/data], ports: ["127.0.0.1:3000:3000"] }
  worker: { image: apex-worker, env_file: .env, volumes: [data:/data], read_only: true, tmpfs: [/tmp] }
  sandbox:{ image: apex-sandbox, network_mode: none, read_only: true, cap_drop: [ALL], pids_limit: 64 }
  tailscale: { image: tailscale/tailscale, … serve https → web:3000 }
volumes: { data: {} }
```

| Environment | Purpose |
|---|---|
| `dev` | local, fake LLM by default, seeded synthetic data |
| `sim` | agent simulations with recorded LLM fixtures (CI) |
| `prod` | your host; migrations run on start with an automatic pre-migration backup |

Upgrades: image tag bump → pre-migration backup → migrate → health check → automatic rollback to the previous image and DB snapshot if the health check fails.
