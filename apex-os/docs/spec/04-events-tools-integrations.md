# 04 — Events, Tools / Skill / Tool Factory, Integrations

## 9. Event architecture (ADR-10, ADR-11)

### Flow

```
command (UI / API / agent tool / integration)
  → validate (zod) → transaction { write events + domain rows + outbox rows }
  → dispatcher (worker; in-process in web for cheap projections)
      → handler(event) : idempotent, ledger(event_id, handler)
          ├─ projection update (read models)
          ├─ metric recompute job
          ├─ trigger evaluation → POLICY → (agent run job | notification | nothing)
          └─ memory/KG mirror
  failures: retry w/ backoff (3×) → dead_letters + inbox WARNING (system)
```

- **Event types** live in a registry `{type, version, schema, domain, description, triggers?}`. Schema evolution uses upcasters (`v1 → v2`), so stored events are never rewritten.
- **Domain event catalog (initial):** `STUDY_SESSION_COMPLETED, ANSWER_RECORDED, TEST_COMPLETED,
  ASSESSMENT_RECORDED, PHONE_LIMIT_EXCEEDED, SCREEN_TIME_RECORDED, SLEEP_RECORDED, ENERGY_RECORDED,
  DEEP_WORK_BLOCK_COMPLETED, TASK_COMPLETED, TASK_CARRIED_OVER, GOAL_AT_RISK, GOAL_DRIFT_DETECTED,
  DEADLINE_APPROACHING, NEW_JOB_FOUND, APPLICATION_STATUS_CHANGED, EMAIL_RECEIVED (metadata only),
  RETENTION_DROP_DETECTED, WEAKNESS_DETECTED, BOTTLENECK_CHANGED, EXPERIMENT_COMPLETED,
  PROJECT_STALLED, FORECAST_CHANGED, CAPABILITY_GAP_DETECTED, AGENT_RUN_COMPLETED, ACTION_VERIFIED,
  APPROVAL_REQUESTED, APPROVAL_DECIDED, BUDGET_EXCEEDED, INTEGRATION_FAILED`.
- **Triggers** are declarative rules: `when: GOAL_AT_RISK where goal.weight ≥ 4 → policy: run agent Orchestrator(task="replan goal") at most 1/day`.
  Every trigger has rate limits and a cooldown. Trigger → policy → agent → action → verification → memory update is traced by `correlation_id`.

### Job queue (ADR-11)

The `jobs_queue` table works as follows:
- workers claim jobs with `UPDATE … SET lease_until=now+ttl WHERE status='ready' AND run_after<=now LIMIT 1 RETURNING`,
- leases are renewed while running,
- retries use exponential backoff with jitter,
- `max_attempts` leads to the DLQ,
- `idempotency_key` is unique.

The scheduler inserts cron jobs with a `(job, period)` key, so restarts never double-run.

- **DECISION.** DB-backed queue.
- **RATIONALE.** No Redis; the queue is transactional with domain writes (outbox pattern for free).
- **ALTERNATIVES.** BullMQ (Redis), pg-boss (Postgres only; adopted on migration).
- **TRADE-OFFS.** Polling latency of about 1 s, which is acceptable.

### Interruption economics (§12 anti-nagging)

Notification decision per item:

```
EV(interrupt) = P(action changes outcome now) × value_at_stake × urgency_decay(deadline)
Cost(interrupt) = context-switch cost (current focus state: deep-work block → high) + daily fatigue(n sent today)
interrupt iff EV > Cost and daily budget remains; otherwise → digest (Inbox, brief)
```

Inbox classes: **CRITICAL** (interrupt-eligible), **IMPORTANT** (next brief), **FYI** (digest only).
The **false alert rate** (items you dismissed as unnecessary) is tracked and tunes the model.

## 12. Tool architecture

### Tool spec

```ts
ToolSpec {
  id, version, kind: "builtin" | "generated" | "integration" | "server" (e.g. Anthropic web_search),
  description, input_schema, output_schema,
  required_permissions[], action_class, side_effects: "none" | "internal" | "external",
  reversible: boolean, undo?: ToolRef, risk_class,
  sandbox_profile?: { net: string[] | "none", fs: "none" | "tmp", cpu_ms, mem_mb, timeout_ms },
  tests_ref, state: "draft" | "sandbox" | "approved" | "deprecated", approved_by?
}
```

`ToolService.invoke(runToken, toolId, input)`:
1. check the capability token,
2. validate input,
3. **PolicyService.evaluate** (action class, risk),
4. execute in the right context (builtin in-process; generated in the sandbox; integration through its adapter),
5. validate output,
6. record an `actions` row and the verification expectation,
7. return.

Tool output given to models is wrapped as data and capped in size; external text is labeled `untrusted`.

### Built-in tool families (v1)

`apex.read.*` (status, domain detail, metrics, KG query, memory search), `apex.write.*` (plan items,
tasks, memory, KG edges, findings, leads, experiments), `research.*` (web search/fetch via provider
server tools, structured extraction), `docs.*` (parse PDF/DOCX, generate CV variants as documents),
`calendar.*`, `email.read`, `email.draft` (draft only), `analysis.run(AnalysisPlan)`,
`code.run` (sandbox, for Data Scientist and Coding agents).

### Tool Factory (§30, ADR-17)

```
NEED ("I need a tool that does not exist")  ── from agent request_capability or Factory gap analysis
→ DEFINE ToolSpec (schemas, permissions ≤ requester ceiling, side_effects, sandbox_profile)
→ GENERATE CODE (Coding Agent, TypeScript for Deno, no external deps unless allow-listed)
→ STATIC CHECKS (typecheck, lint, banned APIs: eval, child_process, fs outside tmp, raw sockets)
→ TESTS (generated + required property tests; fixtures; must reach coverage ≥ 80% of branches)
→ SANDBOX RUN (deny-by-default: network only to declared hosts, no fs except tmp, CPU/mem/time caps)
→ SECURITY CHECK (Critic + rules: permission minimality, data egress analysis, secrets access = forbidden)
→ HUMAN APPROVAL if side_effects ≠ "none" OR net ≠ "none" OR risk_class ≥ medium
→ REGISTER (state approved, version pinned, hash recorded) → USE
```

- Generated tools **never** get secrets; they call integration adapters through tool references if they need authenticated access, which preserves least privilege.
- Generated tools are **never** deployed into core infrastructure code paths. They run only in the sandbox.

**ADR-17.**
- **DECISION.** Deno subprocess with explicit permission flags inside a hardened container (`network_mode` limited by an egress proxy allow-list, read-only rootfs, no capabilities).
- **RATIONALE.** Deno's permission model is capability-based and matches our model; the container adds defense in depth.
- **ALTERNATIVES.**
  - Node `vm` module: not a security boundary.
  - WASM (QuickJS): strong isolation, but limited libraries.
  - Firecracker microVMs: strongest isolation, but heavy.
- **TRADE-OFFS.** A Deno runtime is an extra dependency; there is cold-start latency (≈100 ms).

### Skill Factory (§17, ADR-18)

Hierarchy: **TOOLS** (atomic) → **SKILLS** (reusable workflows) → **AGENTS** (reasoning entities using tools and skills).

```ts
SkillSpec {
  id, version, description, input_schema, output_schema,
  steps: Array<
    | { kind: "tool"; tool: ToolRef; args: Template }
    | { kind: "llm"; tier: Tier; prompt_template: string; output_schema }
    | { kind: "branch"; if: Expr; then: StepId; else: StepId }
    | { kind: "map"; over: Expr; step: StepId; max: number }
  >,
  required_permissions[] (= union of steps, checked ≤ caller),
  fixtures[], success_criteria[], cost_estimate
}
```

Example: `research_company(org)` = web_search → fetch top N pages → extract (structured) → verify facts
(cross-check) → summary with citations. The Company Intelligence Agent uses it.

The Skill Factory mines **procedural memory** and run traces for repeated successful step sequences
(≥ 3 occurrences with the same shape), proposes a skill, tests it on recorded fixtures and registers it.
Skills inherit the caller's permissions: a skill cannot exceed its caller.

### Decision rule (§16): agent vs tool vs skill vs team

| Need | Create |
|---|---|
| new *reasoning specialization* (judgement, domain expertise, planning style) | AGENT |
| new *functionality* (an API, parser, computation) | TOOL |
| recurring *multi-step procedure* using existing tools | SKILL |
| specialization that needs a missing function | AGENT + TOOL |
| multi-role, time-bounded project | TEAM |

## 13. Integration architecture

### Adapter interface

```ts
interface IntegrationAdapter<Config, Secret> {
  id: string; kind: "source" | "sink" | "both";
  capabilities: string[];               // e.g. ["calendar:read"], ["email:read"], ["email:draft"]
  dataClasses: DataClass[];             // what sensitivity it produces/consumes
  configure(cfg: Config, secretRef: SecretRef): Promise<void>;
  health(): Promise<HealthStatus>;
  sync?(since: Date, emit: (e: RawEventInput) => Promise<void>): Promise<SyncResult>;   // sources
  perform?(action: ExternalAction, token: CapabilityToken): Promise<ActionResult>;      // sinks (gated)
}
```

The core never imports a provider SDK; adapters do. A missing or failing adapter degrades its domain to
`UNKNOWN` and never blocks the core. Failures raise `INTEGRATION_FAILED` (one inbox item per incident,
not per retry).

### Adapter catalog (v1 → later)

| Area | v1 | Later |
|---|---|---|
| Calendar | ICS read (private URL); plan → ICS feed | CalDAV / Google Calendar API (OAuth, write-own-calendar under L3 grant) |
| Email | IMAP read-only, headers + optional body of allow-listed senders (career) | Gmail API drafts (`email.draft`); sending stays constitution-gated |
| Browser | provider web search/fetch (server-side) | Playwright browser-use in sandbox (read-only by default; form submission constitution-gated) |
| Files / cloud | local import folder watch; Google Drive read (OAuth) | Dropbox, OneDrive |
| Job sources | RSS/Atom, Arbeitnow, company career pages via research | Bundesagentur für Arbeit API, LinkedIn (manual export only; no scraping) |
| Learning systems | CSV import (Anki, exam platforms) | Anki-Connect, vendor APIs |
| Phone / screen time | CSV/JSON export import (iOS Shortcuts, Android Digital Wellbeing exports) | companion Shortcut posting to `/api/v1/events` with a device token |
| Health / wearables | Apple Health export.xml, generic CSV | Health Connect (Android companion), Garmin/Oura APIs |
| LLM | Anthropic (primary), optional others | local models |
| MCP | APEX as an MCP server (read and log tools) | APEX as an MCP client: external MCP servers mounted as integration tools, with permission mapping |
| Notifications | ntfy / webhook / Web Push | email digest (draft-to-self) |

**Secrets** live in an encrypted secrets store (`secret_ref`). Only adapters resolve them, never agents or models.
