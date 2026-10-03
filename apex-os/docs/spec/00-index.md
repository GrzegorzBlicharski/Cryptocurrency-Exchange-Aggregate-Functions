# APEX OS — Technical Specification v1.0

**Status:** DRAFT FOR PRINCIPAL APPROVAL · **Supersedes:** `docs/ARCHITECTURE.md` (v0.3 prototype) once accepted
**Owner / principal:** the user. **Scope:** a personal, multi-year system that moves from
*personal analytics* → *personal intelligence* → *AI Chief of Staff* → *controlled autonomous personal organization*.

> North star: **MAXIMUM VERIFIED LONG-TERM PROGRESS PER SUSTAINABLE HOUR OF EFFORT.**
> Constitution: *APEX may grow its own organization. It may never widen the limits of its own authority.*

## Reading order

| # | Document | Covers (numbering of the brief's §57 deliverables) |
|---|---|---|
| 00 | this index | decision register, v0.3 to v1.0 gap analysis, decisions needing approval |
| 01 | [System & functional architecture](01-system-architecture.md) | 1 System, 2 Functional, 17 Repository, 21 Deployment |
| 02 | [Agents, autonomy, permissions, model routing](02-agents-autonomy.md) | 3 Agent architecture (incl. **Agent Factory**), 7 Autonomy, 8 Permissions, 14 Model routing |
| 03 | [Data, memory, knowledge graph, analytics](03-data-memory-analytics.md) | 4 Data, 5 Memory, 10 DB schema, 11 KG schema, 15 Analytics |
| 04 | [Events, tools, skills, integrations](04-events-tools-integrations.md) | 9 Events, 12 Tools (+ Tool/Skill Factory), 13 Integrations |
| 05 | [Security, threats, failure modes, backup](05-security-resilience.md) | 6 Security, 22 Backup/export, 23 Threat model, 24 Failure modes |
| 06 | [API contracts & core TypeScript interfaces](06-contracts.md) | 18 API, 19 TS interfaces |
| 07 | [UI information architecture](07-ui.md) | 16 UI IA |
| 08 | [Testing & implementation roadmap](08-testing-roadmap.md) | 20 Testing, 25 Roadmap |

Every load-bearing choice is recorded as an ADR (`ADR-nn`) with **DECISION · RATIONALE · ALTERNATIVES · TRADE-OFFS**.

## Decision register

| ADR | Decision | Doc |
|---|---|---|
| ADR-01 | Modular monolith with two processes (web, worker) and no microservices | 01 |
| ADR-02 | **TypeScript end to end** (Next.js and Node), rebuilt module by module with v0.3 Python as the reference implementation | 01 |
| ADR-03 | pnpm monorepo: `apps/*` (web, worker, cli) and `packages/*` (domain modules) | 01 |
| ADR-04 | Local-first deployment: one home machine or a private VPS in Docker, reached over a private network (Tailscale), PWA on phone and Chromebook | 01 |
| ADR-05 | SQLite (WAL) through Drizzle ORM; the schema is Postgres-portable; switch when a stated trigger fires | 03 |
| ADR-06 | Raw-event-first data model; every aggregate can be recomputed from events | 03 |
| ADR-07 | Metrics are versioned code plus a DB registry. Every value stores `metric_version` and `inputs_hash` | 03 |
| ADR-08 | Knowledge graph in SQLite (typed nodes and edges, recursive CTEs); no graph database yet | 03 |
| ADR-09 | Hybrid memory retrieval: SQLite FTS5 plus local embeddings (sqlite-vec, on-device model); nothing personal is sent to an embedding API | 03 |
| ADR-10 | Event bus = persistent outbox plus in-process dispatcher, idempotent handlers, dead-letter table | 04 |
| ADR-11 | DB-backed job queue with leases (no Redis); `pg-boss` after the Postgres switch | 04 |
| ADR-12 | Policy Engine is the only path to side effects; the constitution is code-owned and not editable by agents | 02 |
| ADR-13 | Permissions are capability strings; `child ⊆ parent` is enforced at spawn and again at every call | 02 |
| ADR-14 | Every agent exists only as a validated **AgentManifest**, never as a free-form prompt | 02 |
| ADR-15 | Agent Factory: reuse before create, sandbox → limited → production, fitness shown as a vector (no single score) | 02 |
| ADR-16 | Model Router with Anthropic as primary provider behind a provider abstraction; tiers by complexity, risk, latency and cost | 02 |
| ADR-17 | Generated tools run in a deny-by-default sandbox (Deno permissions in a container); registration needs tests, a security scan and, by risk, human approval | 04 |
| ADR-18 | Skills are declarative, versioned workflows (DAG of tool and LLM steps) with fixtures | 04 |
| ADR-19 | Verification layer: every autonomous action carries intent, expectation and observation, plus a verifier chosen by policy | 02 |
| ADR-20 | NL data queries compile to a constrained analysis DSL that runs deterministically; the LLM never produces the numbers | 03 |
| ADR-21 | Field-level encryption (libsodium) plus encrypted backups (age); passkeys for authentication | 05 |
| ADR-22 | Next.js App Router with server components for reads; REST `/api/v1` (zod → OpenAPI) as the stable contract; SSE for live agent runs | 06 |

## Where v0.3 stands vs. this spec

v0.3 (Python/FastAPI, ~8.8k LOC, 78 tests) proved the core loop. It runs in production for the user
until v1.0 reaches parity on each module, and serves as **reference implementation and golden-test oracle**.

| Area | v0.3 (built) | v1.0 (this spec) | Gap |
|---|---|---|---|
| Data model | domain tables (sessions, sleep, …) | raw `events` first, derived metrics versioned | **restructure** |
| Goals | flat goals with weights | 8-level hierarchy, drift and orphan detection | **new** |
| Metrics | computed ad hoc in agents | metric registry (definition, formula, unit, version, confidence) | **new** |
| German / Law | competency, knowledge graph, velocity | error taxonomy, CEFR evidence matrix, readiness and forecast with intervals, error-type diagnosis | **extend** |
| Learning | session outcomes, hypotheses | retention 1/7/30/90/180 d, retained learning per hour | **extend** |
| Weakness / Bottleneck | implicit in agents | dedicated engines with priority formula and cause vs symptom | **new** |
| Experiments | randomized day A/B | baseline, interventions, evidence levels (6-step causality ladder) | **extend** |
| Forecasting | — | base, upside and downside with assumptions | **new** |
| Decisions | decision memory only | Decision Journal with expected-vs-actual calibration | **new** |
| Memory | 7 layers, supersede and forget | 9 types, consolidation, dedupe, decay, contradiction detection, provenance | **extend** |
| Knowledge graph | law areas only | personal KG (24 entity types), queryable by agents | **new** |
| Agents | 11 analysts + 7 mission roles (static) | manifests, **Agent Factory**, registry, lifecycle, fitness, evolution, teams | **new** |
| Autonomy | L0–L4, forbidden list, grants | L0–L5 per (domain × action class), risk, confidence, reversibility and impact scoring | **extend** |
| Permissions | role tool lists | capability strings, inheritance proof, per-run capability tokens | **new** |
| Model routing | single model | tiered router with critic pass for critical work | **new** |
| Tools | fixed Python tools | Tool Registry, **Tool Factory** with sandbox, **Skill Factory** | **new** |
| Verification | provenance checks, status | verification layer with 8 statuses and verifier policy | **extend** |
| Events | append-only log + markers | typed event bus, outbox, idempotency ledger, DLQ, triggers → policies | **extend** |
| Observability | run logs, tokens | AI metrics (verified success, correction rate, cost per success, …) | **new** |
| UI | Jinja server-rendered | Next.js design system, Mission Control, Agent Control Center, Data Explorer | **rebuild** |
| Integrations | ICS, IMAP, Apple Health, jobs, RSS, ntfy, MCP | same, behind adapter interfaces in TS | **port** |
| Security | scrypt, CSP, Fernet fields, audit | passkeys, libsodium, age backups, threat model | **extend** |

## Decisions requiring your approval before implementation

1. **ADR-02 Stack.** Rebuild in TypeScript (Next.js) as your brief prefers, keeping v0.3 Python
   running until each module reaches parity. The alternative is to keep the Python core and add only a
   Next.js UI (see ADR-02 for the trade-offs).
2. **ADR-04 Hosting.** Where APEX lives: your own machine (most private) or a private VPS (always on).
   Either way it is reached over Tailscale, not exposed publicly.
3. **ADR-16 Model policy.** Anthropic as the primary provider. Routine work goes to `claude-haiku-4-5`,
   standard work to `claude-sonnet-5-5`, and deep or critical work to `claude-opus-5-5`.
   `claude-fable-5-1` is used only if you enable it. Monthly cost ceiling: **you set it** (proposed
   default €60/month, hard stop at 100%).
4. **Autonomy defaults.** Every domain starts at **L2 (prepare)**. L3–L5 are granted per domain × action
   class by you. Proposed initial grants:
   - L4 for *internal planning* (your plan items, schedules, experiments),
   - L3 for *research*,
   - L2 for anything that leaves the system.

Once these are approved, Phase 0 starts (see doc 08).
