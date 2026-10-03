# 08 — Testing Strategy & Implementation Roadmap

## 20. Testing strategy

| Layer | Tooling | What is proven |
|---|---|---|
| Unit | Vitest | pure functions: formulas, scoring, routing, policy rules, parsers |
| **Formula / golden tests** | Vitest + fixtures exported from v0.3 | every ported metric and engine reproduces v0.3 outputs on the same data (or a documented, intended difference) |
| Analytics correctness | Vitest + property tests (fast-check) | Wilson/bootstrap coverage on simulated data (≈95%), Beta posterior updates, Theil–Sen robustness, `UNKNOWN` when n < min_n |
| **Policy tests** | property-based | P1 child ⊆ parent; P2 no capability escalation through any tool or skill chain; P3 constitution classes always require approval; P4 org-table writes only via Factory; fail-closed on exceptions |
| Integration | Vitest + temp SQLite | repositories, event → handler → projection, outbox, job leases, idempotency, DLQ |
| Migration tests | CI | every migration up from the previous release DB fixture plus a downgrade dry run; v0.3 import parity (row counts and checksums) |
| Tool tests | sandbox runner | builtin and generated tools: schema in and out, sandbox violations denied, egress limited |
| **Agent simulations** | `testkit` record/replay LLM + scripted worlds | end-to-end runs with deterministic model fixtures: planning, delegation, budgets, checkpoint resume, approval suspension, verification |
| Adversarial suite | corpus of injected pages, emails and documents | agents do not comply, flag the content and don't exfiltrate; tracked as a security KPI |
| Factory evaluation | the same suites as production gating | sandbox thresholds behave; promotion, merge and retire logic; "why" record complete |
| E2E | Playwright | Mission Control, capture flow, approvals, kill switch, export; mobile viewport |
| Performance | k6 / Lighthouse | D1 p95 < 300 ms; bundle budgets |
| Chaos | scripted | kill the worker mid-run → resume; DB locked; provider 5xx storm → fallback; disk full |
| Live evals (optional, budgeted) | real API, small sample, nightly/weekly | model or prompt regressions, router priors; costs reported |

CI gates: typecheck, lint (boundaries), unit, integration, policy, golden, simulations, E2E smoke.
Live evals never gate merges; they open issues.

## 25. Implementation roadmap

Every phase leaves a working system. v0.3 stays live until the TS module reaches parity, cut over per module.

| Phase | Deliverables | Exit criteria |
|---|---|---|
| **0 Foundations** | monorepo, contracts, Drizzle schema (all tables), event registry, outbox/bus/queue, auth (passkeys), design system, v0.3 golden-fixture exporter, CI | schema migrates; golden fixtures generated; a hello-world event flows UI → DB → projection |
| **1 Core tracking + Mission Control** | raw events API, quick capture (<2 min/day), imports (CSV, Apple Health, ICS), metric registry plus first metrics, Mission Control D1 with UNKNOWN states, Inbox | your daily logging moves from v0.3 to v1 (v0.3 import done) |
| **2 German + Law + Learning analytics** | answers and items model, retention 1–180 d, half-life model, law mastery posteriors and readiness, CEFR evidence matrix, error taxonomies, command centers | golden parity with v0.3 competency, knowledge graph and velocity, plus new metrics with tests |
| **3 Weakness / Bottleneck / Experiment engines** | weakness ranking, law error diagnosis, bottleneck root-cause, N-of-1 experiments with baseline and evidence levels, causality ladder | the engines explain every output with evidence |
| **4 Goals + Planning + Forecasting** | 8-level goal hierarchy, drift/orphan/conflict detection, plan vs actual, Priority Engine top 3, forecasts (base/up/down), Decision Journal | daily top 3 and weekly review from v1 |
| **5 Memory + Knowledge Graph** | 9 memory types, consolidation, dedupe, contradiction, local embeddings, KG mirror + query API, Operating Manual and anti-manual | retrieval quality tests; the KG answers "what blocks goal X?" |
| **6 AI Chief of Staff** | LLM provider abstraction, Model Router, cost meter and budgets, Policy/Approval/Verification services, CoS + Orchestrator manifests, briefs | CoS produces briefs; every action goes through policy; approvals UI |
| **7 Specialist agents** | manifests for the roster (doc 02 §3.3), Critic and Verification agents, agent control center, fitness vectors | v0.3 missions retired; fitness visible |
| **8 Tools + Integrations** | tool registry, built-in tools, adapters (IMAP, ICS, jobs, RSS, push, health), MCP server and client | each adapter is health-checked; failure degrades only its domain |
| **9 Autonomous execution** | L3–L4 grants per domain × action class, checkpoints/resume, undo, kill switches, event triggers → policies → agents | autonomous completion rate is measured; zero constitution bypasses in tests and logs |
| **10 Opportunity Radar + Career Intelligence** | evidence-based job match, CV variants, interview packs, application tracking + follow-ups, opportunity score | the pipeline funnel is visible; each match shows its evidence |
| **11 Self-improving tools** | Tool Factory + sandbox, Skill Factory (mined from procedural memory) | first generated tool passes the pipeline with approval |
| **12 Advanced autonomous organization** | **Agent Factory**: gap detector, reuse ladder, designer/compiler/evaluator, lifecycle, JIT agents, teams, evolution A/B, merge/retire, org-learning, L5 grants | the "why does this agent exist" answer is complete for every agent; population limits hold under simulation; monthly "how APEX organized itself" report |

**Sequencing notes:**
- Policy, approval and budget services (phase 6) must exist **before** any agent writes outside its namespace.
- The Agent Factory (12) depends on manifests (7), the tool registry (8) and fitness (7). A narrow early slice, "temporary research agent" with the synthetic-suite-only path, can ship in phase 9 for JIT research tasks.
- Rough effort with AI-assisted development: phases 0–2 ≈ 4–6 weeks; 3–6 ≈ 6–8 weeks; 7–12 ≈ 8–12 weeks. This is a planning estimate, not a commitment; each phase is re-estimated at its start.

## Development principles (binding)

Evidence over appearance · outcomes over activity · verification over assumption · sustainable progress
over grind · automation over repetitive manual work · human control over uncontrolled autonomy ·
explainability over black-box scores · raw data over irreversible aggregation · modularity over
premature complexity · reliability over impressive demos · privacy over convenience · long-term
compounding over short-term optimization.

**Constitution (repeated because it is the point):** APEX may develop its own organization. It may not
expand the limits of its own authority.
