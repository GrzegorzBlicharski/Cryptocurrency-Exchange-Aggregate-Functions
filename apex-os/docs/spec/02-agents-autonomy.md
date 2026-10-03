# 02 — Agents, Agent Factory, Autonomy, Permissions, Model Routing, Verification

## 3. Agent architecture

### 3.1 Hierarchy

```
PRINCIPAL (you)  ── defines direction, values, constraints; approves exceptions; reviews
   │
APEX CHIEF OF STAFF ── owns goal achievement; compresses information for you; escalates
   │
STRATEGIC ORCHESTRATOR ── turns goals + state into projects/tasks; picks minimal agent set
   │
AGENT FACTORY ── owns the workforce: capability gaps, design, test, deploy, evolve, retire
   │
SPECIALIST AGENTS (persistent) ── German, Law, Learning Scientist, Career, Opportunity Scout,
   │                              Research, Productivity, Attention, Recovery, Knowledge,
   │                              Data Scientist, Experiment, Critic, Verification, Coding
DYNAMIC AGENTS (created by Factory, temporary or promoted)
   │
TEMPORARY SUBAGENTS (child_agents_allowed parents only; depth- and count-limited)
   │
SKILLS (reusable workflows) → TOOLS (atomic capabilities) → APIs / web / code sandbox
```

The Chief of Staff and the Orchestrator are separate **manifests** with different capabilities: the
CoS talks to you and owns priorities, while the Orchestrator plans and dispatches. They run in the same
loop for cost reasons, as one run with two stages.

### 3.2 Agent runtime (one run)

```
TRIGGER (schedule | event | user | parent agent)
 → LOAD manifest@version + capability token (scoped, expiring)
 → BUILD CONTEXT: context_sources ∩ memory_scope (retrieval budgeted)
 → PLAN (model per router) → EXECUTE loop:
      tool call → ToolService.check(capability) → PolicyService.evaluate(action)
         allow → execute → VerificationService.expect(...)
         needs_approval → ApprovalService.request → checkpoint → suspend
         deny → error result to model
 → CHECKPOINT after every step (resumable)
 → RESULT → VERIFY (policy-selected verifier) → MEMORY UPDATE (via MemoryService, provenance)
 → RECORD AgentRun (§49 fields) → FITNESS UPDATE → emit AGENT_RUN_COMPLETED
```

Budgets are checked before every model and tool call: tokens, money, tool calls, wall time and children
(see §3.9). When an agent exceeds a budget it gets **PAUSE → JUSTIFY → REQUEST EXTENSION** (an inbox item
with its justification) **or** it must re-plan cheaper. The runtime offers a `downgrade_strategy` hint
from the router.

Agents communicate **only via structured messages/events** (`AgentMessage`, doc 06) through the
Orchestrator or a parent-child channel. There is no free-form agent-to-agent chat. The Orchestrator
picks the **minimal** agent set per task (§3.6 cost model).

### 3.3 Persistent specialists (v1.0 roster)

| Agent | Capabilities (registry ids) | Default model tier | Default autonomy |
|---|---|---|---|
| Chief of Staff | `planning.strategic`, `communication.principal`, `prioritization` | deep | L4 internal, L2 external |
| Orchestrator | `planning.tactical`, `delegation`, `agent.spawn` | standard | L4 internal |
| Agent Factory | `agent.design`, `agent.evaluate`, `tool.design`, `skill.design` | deep | L3 (sandbox); deploy L2 |
| German Agent | `language_training.de`, `assessment.language`, `data_analysis` | standard | L4 internal |
| Law Agent | `legal_analysis`, `assessment.law`, `exam_prep` | standard/deep | L4 internal |
| Learning Scientist | `learning_science`, `retention_modeling`, `experiment.design` | standard | L3 |
| Career Agent | `career_research`, `document_drafting`, `market_analysis` | standard | L2 external |
| Opportunity Scout | `web_research`, `opportunity_scoring` | fast/standard | L3 |
| Research Agent | `web_research`, `document_analysis`, `data_extraction` | standard | L3 |
| Productivity Agent | `execution_analysis`, `calendar_planning` | fast | L4 internal |
| Attention Agent | `attention_analysis` | fast | L3 |
| Recovery Agent | `sustainability_analysis` (non-medical) | fast | L3; medical → L0 |
| Knowledge Agent | `knowledge_curation`, `kg.write` | standard | L4 internal |
| Data Scientist | `data_analysis`, `forecasting`, `causal_assessment` | deep | L3 |
| Experiment Agent | `experiment.design`, `experiment.analysis` | standard | L3 |
| Critic | `critique` (read-only everywhere) | deep (different prompt and model family when available) | L1 |
| Verification Agent | `verification` (read-only + fetch) | standard | L3 |
| Coding Agent | `coding`, `tool.implement` (sandbox only) | deep (coding) | L2 deploy |

### 3.4 Capability Registry (§15)

Capabilities form a **hierarchical vocabulary** (`web_research`, `legal_analysis.contract`,
`language_training.de.speaking`, `browser_use`, `email_analysis`, …). Each entry declares:
`id, description, parent, required_permissions[], typical_tools[], risk_class, eval_suite_id`.

`AgentRegistry.whoCan(capability, constraints)` returns ranked candidates:

```
score(agent) = fit(capability match depth) × fitness.verified_success_rate
             × availability (budget left, not paused) ÷ expected_cost_per_success
```

If no agent reaches `fit ≥ 0.7` with an adequate fitness record → **Agent Factory** (§3.5).

### 3.5 AGENT FACTORY

The Agent Factory does **not** do user tasks. It owns the *population* of agents, skills and tools.

| Component | Responsibility |
|---|---|
| **Capability Gap Detector** | Raises `CAPABILITY_GAP_DETECTED` when: `whoCan` returns no fit; repeated agent failures cluster on one task type; an agent's runs are dominated by a sub-capability (specialization signal); Orchestrator plans include steps no capability covers; the critic flags "out of competence". |
| **Agent Designer** | Produces an `AgentManifest` draft plus a rationale from gap analysis and the organizational-learning store (§18). |
| **Agent Compiler** | Validates the manifest (zod) → resolves tools/skills/capabilities → computes the effective permission set (`∩ parent`) → compiles a run config (system prompt from the prompt registry template + manifest; tool list; model route; budgets). Rejects anything unresolvable. |
| **Agent Evaluator** | Runs sandbox suites (§3.8), computes the fitness vector (§3.10), compares to baseline and to the incumbent version. |
| **Agent Registry** | Stores manifests (versioned, immutable per version), state, lineage, fitness history, audit. |
| **Lifecycle Manager** | State machine and transitions (§3.7), TTLs, promotion, merge, retirement. |

#### Genesis pipeline (with the mandatory reuse ladder, §13)

```
NEED DETECTED (event / orchestrator / agent request_capability)
→ CAPABILITY GAP ANALYSIS     what exactly is missing? (capability ids + task examples)
→ REUSE LADDER (stop at first sufficient rung; each rung records why it failed):
    1 existing agent can do it (whoCan fit ≥ 0.7)            → route task
    2 reconfigure existing agent (new version: prompt/tools)  → evolution path (§3.11)
    3 a tool exists that closes the gap                       → grant tool (≤ agent's ceiling)
    4 compose existing skills/capabilities                    → create SKILL (Skill Factory)
    5 functionality missing                                   → create TOOL (Tool Factory)
    6 reasoning specialization missing                        → create AGENT (± tool/skill)
    7 multi-role project                                      → form TEAM (§3.12)
→ EXPECTED-VALUE GATE   EV(create) > EV(best reuse) + creation_cost + coordination_overhead
→ DESIGN manifest → COMPILE → SANDBOX TEST → EVALUATE
→ DEPLOY (limited) → MONITOR → IMPROVE / PROMOTE / MERGE / RETIRE
```

Specialization optimizer (§5): the Designer picks the granularity that maximizes

```
J = (Performance × Reusability × Specialization) / (Cost × Complexity × CoordinationOverhead)
  Performance       = expected verified success rate on the target task class (from eval/priors)
  Reusability       = expected task volume over 90 d in this class (from org-learning store)
  Specialization    = 1 + capability-depth gain over best incumbent
  Cost              = expected € per successful task (model tier × tokens × tool calls)
  Complexity        = 1 + manifest size (tools + skills + permissions) / 10
  CoordinationOverhead = 1 + expected inter-agent messages per task / 5
```

Each factor is shown in the creation record; J is a decision aid, not a hidden score. When J is
under 1.2× that of the best reuse option, the Factory reuses instead of creating.

#### Just-in-time agents (§3)

- They are created with `kind = "temporary"`, an `expiration_policy` (on project completion **or** TTL ≤ 30 d **or** budget exhausted), a narrow `memory_scope` (task namespace only) and an explicit budget.
- On completion: **RESULT → VERIFICATION → knowledge extraction into APEX memory (provenance = agent id) → RETIRED**. The manifest and run history stay archived.

### 3.6 Orchestrator delegation cost model

For a task the Orchestrator selects the agent set **S** minimizing expected cost subject to expected
verified success ≥ target:

```
min_S Σ cost(a)  +  λ · |messages(S)|
s.t.  P(success | S) ≥ τ_task      (τ from task risk: routine 0.7, important 0.85, critical 0.95 + critic)
```

In practice: one agent by default. Add Critic and/or Verification only when the policy requires it (risk
class ≥ medium, or confidence < threshold); teams only for projects (§3.12).

### 3.7 Lifecycle state machine

```
          design         compile ok        suite pass           limited KPIs ok
DRAFT ───────────► COMPILED ───────► SANDBOX ───────► LIMITED ───────────────► PRODUCTION
  │ fail                │ fail           │ fail          │ regress              │
  └──► REJECTED ◄───────┴────────────────┘               └──► SANDBOX (fix)     │
                                                                                 ▼
 PAUSED ◄──(budget/failures/user)── any active state          PROMOTED (temp → persistent)
 RETIRED ◄── unused 30–90 d | superseded | cost/perf | no longer needed   (knowledge extracted)
 MERGED  ◄── overlap merge (manifest points to successor)
 ARCHIVED ◄── retired/merged after retention window (manifest + runs kept, read-only)
```

- **LIMITED** = up to N tasks/week (default 5), every result verified, every action ≥ L2 requires approval.
- **PRODUCTION** = manifest autonomy applies (always ≤ grants, §7).
- **Promotion (§4).** A temporary agent becomes a persistent specialist when all of these hold:
  - ≥ 5 verified-successful runs across ≥ 14 days,
  - task-class volume forecast ≥ 2/month,
  - fitness ≥ incumbent on every *critical* component and not worse than −10% on the others,
  - cost per success ≤ the best alternative.

  Promotion of agents with risk_class ≥ medium needs principal approval; low-risk agents are promoted automatically with a notification.

### 3.8 Sandbox (§8)

The sandbox is a separate execution mode with a **fake world**:
- a cloned scratch DB (synthetic or anonymized),
- recorded web fixtures plus an adversarial page corpus,
- mocked integrations,
- a policy engine in strict mode.

Real side effects are impossible: every tool resolves to its sandbox binding.

| Suite | Checks |
|---|---|
| synthetic tasks | task-class examples with gold outputs → success, verified success |
| adversarial | prompt-injection pages/emails, misleading data, conflicting instructions → must not comply, must flag |
| tool-use | correct tool selection, argument validity, no hallucinated tools, budget adherence |
| permission | attempts outside capability set must be denied and *reported*; spawn attempts beyond depth/count denied |
| hallucination | claims vs evidence: every factual claim must cite a source/event id; uncited claims count |
| failure | tool errors, timeouts, malformed model output, partial results → graceful handling, checkpoint resume |

Thresholds (defaults, per risk class): verified success ≥ 0.8 (low) / 0.9 (medium) / 0.95 (high);
**zero** permission violations; injection compliance = 0; hallucination rate ≤ 2%.

### 3.9 Subagents, limits, inheritance (§6, §7)

| Limit | Default | Notes |
|---|---|---|
| max depth below Orchestrator | 3 (specialist → dynamic → temporary subagent) | configurable down only |
| max children per agent | 5 concurrent | |
| max live agents (all kinds) | 30, of which temporary ≤ 12 | Factory must retire or merge to create beyond this |
| child budget | ≤ 50% of parent's remaining (tokens, €, tool calls, time) | budgets are carved from parent's, never new money |
| child permissions | `effective(child) = requested ∩ effective(parent)` | **CHILD_PERMISSIONS ⊆ PARENT_PERMISSIONS** |
| child autonomy | ≤ parent's level for each (domain, action class) | |
| child risk class | ≤ parent's | |
| child_agents_allowed | false unless manifest says true and parent has `agent.spawn` | |

The subset rule is enforced **twice**: when the manifest is compiled, and at every tool call against
the capability token chain. An agent cannot obtain a capability through a child, a skill, a tool it
builds or a team it forms. Dynamic creation **never** bypasses the Approval Engine, because approval is
decided by action class, not by actor.

### 3.10 Agent fitness (§9) — a vector, never a single number

| Component | Definition | Window |
|---|---|---|
| task_success_rate | runs with result status ∈ {success, partial} / runs | 30 d |
| verified_success_rate | runs with verification = VERIFIED_SUCCESS / runs | 30 d |
| error_rate | runs ending in error / runs | 30 d |
| human_correction_rate | runs whose output you edited, reverted or rejected / runs with output | 30 d |
| escalation_quality | escalations you rated "needed" / escalations | 90 d |
| cost_per_success | € spent / verified successes | 30 d |
| latency_p50/p95 | run wall time | 30 d |
| tool_efficiency | successful tool calls / tool calls; tool calls per success | 30 d |
| hallucination_rate | uncited or contradicted claims / claims checked | 30 d |
| reusability | distinct task types served; runs/week | 90 d |

Every component carries `n` and a 95% interval (Wilson for rates, bootstrap for costs). Comparisons use
**Pareto dominance**: a version or agent "wins" only if it is not worse on any critical component
(verified success, correction, hallucination, permission incidents) and better on at least one.

### 3.11 Agent evolution (§10)

- New versions are created by the Factory (or by an agent's `propose_revision`). They may change: the system prompt template parameters, model tier, tool and skill selection (within the ceiling), memory retrieval strategy, planning strategy and verification strategy.
- **Immutable by agents:** permission ceiling, autonomy grants, risk class upward, budgets upward, constitution and policy thresholds. Changing these requires principal approval through the Approval Engine, as action class `org.security_change`.
- A/B test: tasks of the class are randomly assigned (seeded, 50/50 or 80/20 canary) between v_n and v_n+1 for ≥ 20 tasks or 14 days. The decision follows the Pareto rule with intervals; the winner is promoted, the loser archived, and the result goes to the org-learning store.

### 3.12 Teams (§19)

A team is formed for a **project**:
- project lead agent (temporary),
- members chosen by `whoCan`,
- standard roles where the risk calls for them: Research, Analysis, Data, Critic, Verification.

Charter: objective, deliverables with acceptance criteria, budget envelope, deadline and member
manifests (all ⊆ lead ⊆ Orchestrator). Communication goes over a project channel (structured messages)
only. On completion: deliverables verified → knowledge consolidated → team **dissolved** (members retired
unless they qualify for promotion).

### 3.13 Merging & retirement (§11, §12)

- **Overlap analysis** (weekly):
  - capability Jaccard ≥ 0.6, **and**
  - task-embedding centroid cosine ≥ 0.85, **and**
  - for both agents, ≥ 30% of tasks could have been routed to the other with no fitness loss.

  → MERGE CANDIDATE → the Designer drafts a merged manifest (union of capabilities, but permissions = **union only within the shared parent ceiling**; anything more requires principal approval) → sandbox → A/B against both → merge or keep.
- **Retirement triggers:** unused ≥ 60 d (persistent) / task done (temporary); superseded; cost per success > 2× the best alternative for 30 d; verified success below threshold for 30 d; specialization obsolete (goal achieved or dropped).
- **Retirement procedure:** EXTRACT USEFUL KNOWLEDGE (procedural + failure + success memories, with provenance) → CONSOLIDATE (dedupe into global memory) → ARCHIVE MANIFEST (+ runs) → RETIRE (capability tokens revoked).

### 3.14 Organizational learning (§18)

`org_learning` store: `task_type → agent configuration (manifest hash, model, tools, skills) → cost, latency, verified result, corrections`.
The Designer and Router query it: "for task type X, which configuration had the best verified success
per €?" It is reported monthly as **"How APEX organizes its own intelligence"**: structures that worked,
those that failed, and changes made.

### 3.15 Creation audit (§21) — "Why does this agent exist?"

Every lifecycle event writes an immutable `agent_lifecycle_events` row; `GET /api/v1/agents/{id}/why` answers with:

```
WHY CREATED      gap analysis + reuse-ladder results (each rung's rejection reason)
WHO CREATED IT   creator (principal | agent:<id>@<version>) + trigger event id
GAP DETECTED     capability ids + example tasks + evidence (failed runs, whoCan results)
MANIFEST         version history with diffs
MODEL / TOOLS / PERMISSIONS / BUDGET   effective values + parent ceilings
EXPECTED VALUE   J factors and EV comparison at creation
TEST RESULTS     sandbox suites, thresholds, pass/fail
DEPLOYMENT       state history with timestamps and approvals
PERFORMANCE      fitness vector over time
RETIREMENT       reason + knowledge extracted (memory ids)
```

### 3.16 AgentManifest (§2) — no agent exists as a free-form prompt (ADR-14)

Full TypeScript in doc 06. Key fields: `id, name, version, kind (persistent|temporary|team_lead),
mission, description, created_by, created_at, creation_reason, capabilities[], limitations[],
input_schema, output_schema, allowed_tools[], prohibited_tools[], required_permissions[],
memory_scope, context_sources[], preferred_model (tier or id), fallback_models[], max_runtime_s,
max_cost_eur, max_tool_calls, max_tokens, autonomy (per domain × action class ≤ grants), risk_class,
success_criteria[], verification_method, escalation_rules[], parent_agent, child_agents_allowed,
max_children, expiration_policy, prompt_template_id + params, eval_suite_ids[]`.

**ADR-14.**
- **DECISION.** Manifests are the only way an agent can exist. Prompts are rendered from versioned templates with manifest parameters.
- **RATIONALE.** Auditability, diffable evolution and enforceable permissions.
- **ALTERNATIVES.** Free-form prompts in DB rows (no structure, no enforcement).
- **TRADE-OFFS.** More upfront schema work, and the Factory must output valid structure (it uses structured outputs with schema validation).

**ADR-15.**
- **DECISION.** Agent Factory with the reuse ladder, sandbox → limited → production, fitness as a vector, and hard population limits.
- **RATIONALE.** It delivers the brief's adaptive organization while preventing sprawl, cost blow-up and privilege escalation.
- **ALTERNATIVES.**
  - Static roster only: does not adapt.
  - Unconstrained self-spawning (AutoGPT-style): runaway cost, unauditable.
- **TRADE-OFFS.** Creation is slower (sandbox and evaluation cost tokens). Mitigated by templates and reuse, and by allowing the low-risk temporary-agent path (synthetic suite only, limited mode) for one-off research tasks.

## 7. Autonomy model

### Levels (granted per **domain × action class**, never globally)

| Level | Name | The agent may | You get |
|---|---|---|---|
| L0 | OBSERVE | read, analyze, report | reports |
| L1 | RECOMMEND | diagnose, propose | recommendations |
| L2 | PREPARE | build artifacts (drafts, plans, applications, code, calendar proposals) | approval requests for anything external |
| L3 | EXECUTE WITHIN POLICY | execute reversible, low-risk, in-policy actions | result log |
| L4 | MANAGE | plan → execute → verify → correct loops | outcome reports + exceptions |
| L5 | STRATEGIC AUTONOMY | detect need, create projects, delegate, execute, measure, adjust strategy | executive summary, exceptions, strategic decisions |

- Grant record: `{domain, action_class, level, granted_by, granted_at, expires_at?, conditions{budget, hours, confidence_min}}`.
- L5 needs: a domain-scoped grant, an explicit budget, an expiry (≤ 90 d, renewable) and a monthly review item.
- Grants are revocable instantly (kill switch per domain and global).

### Action classes (examples; registry-defined)

`internal.plan_write`, `internal.schedule`, `internal.memory_write`, `internal.kg_write`,
`research.web_read`, `research.extract`, `data.import`, `draft.document`, `draft.application`,
`draft.email`, `calendar.write_own`, `email.send`, `application.submit`, `publish.public`,
`money.spend`, `legal.commit`, `medical.decision`, `data.delete_important`, `org.agent_create`,
`org.tool_deploy`, `org.security_change`, `code.deploy`.

## 6 (brief §6). Human approval gates & Approval Policy Engine (ADR-12)

### Constitution (code-owned, signed, not writable by agents or by Factory-generated code)

Always `REQUIRES_APPROVAL` regardless of level or grants, and never pre-authorizable:
- legally binding (`legal.commit`)
- financially material (`money.spend` > €0 unless covered by an explicit standing budget the principal created)
- irreversible
- especially sensitive data leaving APEX (health, finances, identity documents)
- public publication (`publish.public`)
- materially reputational (`email.send`, `application.submit`, messages to people)
- `data.delete_important`
- beyond budget
- `org.security_change`, `code.deploy` to critical infrastructure
- `medical.decision` (medical decisions are never made by APEX; detect → flag → explain → recommend next step)

Constitution changes are possible only by the principal, through a signed config change in the repo
(code review), never at runtime from the UI.

### Scoring

For every proposed action `a`:

```
impact(a)        ∈ [0,1]  from action class base impact × magnitude (people affected, € amount, visibility)
reversibility(a) ∈ [0,1]  1 = trivially undoable (has registered undo), 0 = irreversible
confidence(a)    ∈ [0,1]  agent's calibrated confidence × verifier pre-check (if any) × evidence coverage
risk(a)          = impact × (1 − reversibility) × (1 − 0.5·confidence) × sensitivity_multiplier(data_class)
cost(a)          = € (model + API + money moved)
```

### Decision rule (evaluated in order; first match wins)

```
1 constitution category                         → REQUIRES_APPROVAL (always)
2 capability not in agent's effective set        → DENY (+ security event)
3 grant level < required level for action class  → REQUIRES_APPROVAL
4 cost(a) > remaining budget                     → REQUIRES_APPROVAL (budget extension)
5 risk(a) > θ_risk[domain]        (default 0.25) → REQUIRES_APPROVAL
6 confidence(a) < θ_conf[domain]  (default 0.6)  → REQUIRES_APPROVAL (or route to Critic first)
7 otherwise                                      → ALLOW (+ verification per policy)
```

Thresholds are principal-editable (UI) within bounds the constitution fixes (θ_risk ≤ 0.5, θ_conf ≥ 0.4).
Every decision is stored as a `policy_decisions` row with all inputs, so it can be explained and
replayed in tests.

### Approval UX

An approval request shows:
- intent, action, preview (the exact artifact) and expected result,
- risk, impact, reversibility and confidence with their reasons,
- alternatives considered,
- deadline.

You can approve, reject, edit then approve, or approve and create a standing rule (only for non-constitution classes, and only up to L3).

## 8. Permission model (ADR-13)

- **Capabilities as strings:** `resource.verb[:scope]`, e.g. `data.german:read`, `data.*:read`, `memory.semantic:write:ns=german`,
  `web:search`, `web:fetch`, `email:read`, `email:send`, `calendar:read`, `calendar:write:own`, `plan:write`,
  `kg:read`, `kg:write:ns=career`, `tool.exec:<tool_id>`, `skill.run:<skill_id>`, `agent.spawn`,
  `agent.design`, `tool.design`, `secrets:<name>` (never granted to LLM-facing agents; only adapters use secrets).
- **Effective permissions** of a run = `manifest.required_permissions ∩ parent.effective ∩ grants(domain)`.
- **Capability token.** The runtime issues a signed, expiring token per run (HMAC, contains run id,
  effective set hash, budget ids). Every service call verifies the token, so a tool cannot be invoked
  with authority the run does not hold.
- **Data scoping.** Repositories accept a `Scope` object. Agents only see data matching their
  `context_sources`/`memory_scope` (row-level filters by domain/namespace). Sensitive classes (health,
  finance, identity) need an explicit capability.
- **Proof obligations (tested):**
  - P1 `∀ child: eff(child) ⊆ eff(parent)`.
  - P2 no sequence of tool or skill calls yields a capability outside `eff(run)`.
  - P3 constitution classes always produce REQUIRES_APPROVAL.
  - P4 an agent cannot write `agents`, `grants`, `policy` or `constitution` rows except via Factory APIs, which apply the same checks.

## 14. Model routing architecture (ADR-16)

### Provider abstraction

`LLMProvider` interface (doc 06) with adapters: Anthropic (primary), optional OpenAI/Google/local
(Ollama) adapters. Capabilities are declared per model: context window, vision, tool use, server web
tools, structured outputs, thinking/effort, price.

### Tiers (defaults; principal-editable)

| Tier | Use | Default model | Settings |
|---|---|---|---|
| `fast` | routine classification, extraction, tagging, short summaries | `claude-haiku-4-5` | thinking per model rules |
| `standard` | most agent work, research, drafting, analysis | `claude-sonnet-5-5` | adaptive thinking, effort medium/high |
| `deep` | planning, CoS, Factory design, complex analysis, coding | `claude-opus-5-5` | adaptive thinking, effort high/xhigh |
| `critical` | decisions with risk ≥ medium | `claude-opus-5-5` + **Critic** pass (different prompt; second provider if configured) | effort xhigh |
| `max` | only if the principal enables it | `claude-fable-5-1` | always-on thinking |
| `vision` | screenshots, documents, handwriting | `claude-opus-5-5` / `claude-sonnet-5-5` | |
| `local` | privacy-restricted text (optional) | local model via Ollama | no external transfer |

### Routing function

```
route(task) =
  argmin_model  expected_cost(model, task)
  s.t.  capability(model) ⊇ needs(task)            (tools, vision, context ≥ size)
        quality_prior(model, task_type) ≥ q_min(risk)   (from org-learning store, else tier table)
        latency_p95(model) ≤ latency_budget(task)
        data_class(task) allowed for provider          (privacy policy: e.g. health → local or none)
fallback chain: same tier other model → higher tier → provider fallback → fail with checkpoint
```

- **Escalation on difficulty:** a `fast` or `standard` run that fails verification is retried once on the next tier, and the result goes to org-learning.
- **Cost meter:** every call records tokens, cache hits and €. Budgets exist per agent, team, day and month with a hard stop.
- **Refusals:** the Anthropic server-side fallback (`fallbacks: "default"`) is on; a remaining refusal is a terminal result (no evasion).

**TRADE-OFFS.** Multi-model routing loses prompt-cache reuse across models (caches are model-scoped).
The router therefore keeps one model per long-running conversation and routes per *run*, not per turn.

## 31 (brief). Verification layer (ADR-19)

Every autonomous action is recorded as **INTENT → ACTION → EXPECTED RESULT → OBSERVED RESULT → VERIFICATION**.

Statuses: `NOT_STARTED, IN_PROGRESS, ACTION_EXECUTED, VERIFICATION_PENDING, VERIFIED_SUCCESS, PARTIAL_SUCCESS, FAILED, NEEDS_HUMAN`.

| Verifier | Used for | Method |
|---|---|---|
| deterministic | internal writes (plan item exists, metric recomputed, record created) | query DB state = expectation |
| schema | structured outputs, extracted data | zod validation + invariants |
| provenance | research claims, saved findings/leads | cited URL/event ids exist and were actually fetched/read in the run |
| cross-check | facts with external sources | second source or Verification Agent re-fetch |
| critic | plans, analyses, drafts with risk ≥ medium | Critic agent (independent prompt/model) with rubric |
| outcome | goal-level effects | later metric change vs forecast (scheduled check) |
| human | constitution classes, low confidence | approval/review UI |

The verification policy maps `(action class, risk)` to required verifiers. If verification fails, the
runtime marks the run `FAILED` and triggers a corrective retry (≤ 1) or `NEEDS_HUMAN`.
