# 03 — Data, Memory, Knowledge Graph, Analytics

## 4. Data architecture

```
 RAW LAYER (immutable, append-only)      events · documents (blobs) · agent_runs.observations
        │ handlers (idempotent, versioned)
 DERIVED LAYER (recomputable)            sessions · metric_values · weaknesses · bottlenecks ·
        │                                forecasts · projections (dashboard read models)
 KNOWLEDGE LAYER (curated, provenance)   memories · knowledge_nodes/edges · operating manual
 OPERATIONAL LAYER                       goals · projects · tasks · experiments · decisions ·
                                         agents · grants · approvals · jobs · audit
```

### ADR-05 SQLite → Postgres-portable

- **DECISION.** SQLite (WAL, `synchronous=NORMAL`, FTS5, sqlite-vec) through **Drizzle ORM**, with schemas written in the portable subset: ISO timestamps as text in UTC, JSON as text with zod validation, no SQLite-only SQL outside repository adapters.
- **Migration triggers to PostgreSQL:**
  - more than one concurrent writer process beyond web+worker,
  - DB size > 20 GB,
  - multi-device sync needs.
- **RATIONALE.** Local-first, zero-ops, a single-file backup, fast enough for one person (millions of events).
- **ALTERNATIVES.**
  - Postgres from day one: more ops, a weaker local-first story.
  - DuckDB for analytics: a possible later read-replica for heavy analysis.
- **TRADE-OFFS.** Single-writer limits (fine here). Vector search is less mature than pgvector; it is isolated behind `MemoryIndex`.

### ADR-06 Raw events first

- **DECISION.** Every observation (manual, integration, agent) is first an `events` row. Domain tables (sessions, answers, sleep…) are *projections* rebuilt by handlers. Aggregates are never the only copy.
- **RATIONALE.**
  - New metrics and corrected formulas can be recomputed over history.
  - It gives a full audit trail.
  - Corrections become new events (`*.corrected` / `*.retracted`), not overwrites.
- **ALTERNATIVES.** CRUD domain tables (v0.3): simpler, but loses history and recomputability.
- **TRADE-OFFS.** Projection rebuild logic and event schema versioning (upcasters) are needed.

### Event record

```
events(
  id TEXT PK (ULID), ts TEXT (when it happened, UTC), recorded_at TEXT,
  type TEXT, type_version INT, domain TEXT,
  source TEXT ('manual'|'integration:<id>'|'agent:<id>@<v>'|'import:<id>'|'system'),
  subject_id TEXT NULL,            -- e.g. session id, answer id, entity id
  duration_s INT NULL, value REAL NULL, unit TEXT NULL,
  payload_json TEXT,               -- validated by registry schema (type, version)
  confidence REAL DEFAULT 1.0,     -- manual 1.0, inferred < 1.0
  idempotency_key TEXT UNIQUE,     -- source-provided dedupe key
  correlation_id TEXT NULL,        -- run/command that produced it
  supersedes_id TEXT NULL,         -- correction chain
  sensitivity TEXT DEFAULT 'normal' -- normal|personal|health|finance|identity
)
```

## 10. Database schema (Drizzle; abbreviated DDL — `id` ULID text PKs, `created_at/updated_at` everywhere)

**Core observation**
- `events` (above) · `event_types(type, version, schema_json, domain, description)` · `documents(id, kind, mime, sha256, blob_path, encrypted, source, title, created_at, sensitivity)`
- `sessions(id, domain, kind, started_at, ended_at, minutes, mode (active|passive), method, material_id, focus, fatigue, interruptions, start_delay_s, task_complexity, output_ref, source_event_id)`
- `answers(id, domain, area, topic, item_id, correct, time_s, confidence_self, error_type, session_id, ts)`, the atomic unit for law and German retrieval
- `items(id, domain, area, topic, kind (question|vocab|grammar_rule|statute|concept), content_ref, difficulty)`, the learnable items behind the retention model
- `item_states(item_id, half_life_h, last_review_at, p_recall_now, reviews, lapses, model_version)`
- `errors(id, domain, taxonomy_code, item_id, answer_id, description_enc, ts)` · `error_taxonomy(code, domain, parent, label, description)`
- `assessments(id, domain, kind (test|mock_exam|cefr_task|speaking_eval|writing_eval), area, score, max, rubric_json, evaluator (self|agent|external), ts, evidence_doc_id)`

**Metrics & analytics**
- `metric_definitions(id, name, domain, definition, formula_ref, unit, direction (up_good|down_good), source_events[], version, min_n, confidence_method, created_at)`
- `metric_values(id, metric_id, version, scope (day|week|month|rolling_7d|…), period_start, value, n, ci_low, ci_high, inputs_hash, computed_at, status (ok|unknown|insufficient))`
- `projections(name, key, json, version, updated_at)` (dashboard read models)

**Strategy**
- `goals(id, parent_id, level (life|outcome_3_5y|annual|quarterly|monthly|weekly|daily), title, why, domain, metric_id, target_value, target_date, weight, status, owner (principal|agent:<id>), created_at)`
- `projects(id, goal_id, title, status, lead_agent_id, budget_id, deadline, acceptance_criteria_json)`
- `tasks(id, project_id, goal_id, title, domain, planned_minutes, priority, status, due, scheduled_for, actual_minutes, completed_at, carry_over_count, abandoned_reason, start_delay_s, source)`
- `weaknesses(id, domain, category, taxonomy_code, evidence_json, confidence, severity, recurrence, impact, trend, suspected_cause, intervention_id, priority, status, first_seen, last_seen)`
- `bottlenecks(id, kind, scope, evidence_json, symptom_of_id, root_cause_confidence, detected_at, status)`
- `experiments(id, hypothesis, baseline_start, baseline_end, intervention_id, design (ab_days|abab|pre_post), schedule_json, metrics[], min_n, status, result_json, evidence_level, decision)`
- `interventions(id, target_weakness_id, description, protocol_json, status)`
- `opportunities(id, kind, title, url, source, retrieved_at, deadline, goal_ids[], score_components_json, score, status, evidence_json, injection_flags)`
- `decisions(id, title, context, options_json, assumptions_json, expected_outcome, confidence, risks, final_choice, decided_at, review_at, actual_outcome, calibration_error)`
- `forecasts(id, target (goal|metric|exam|skill), target_id, method, horizon, base, upside, downside, assumptions_json, evidence_json, confidence, computed_at, model_version)`
- `recommendations(id, fact_json, interpretation, action, why_json, priority_components_json, status, created_at)`

**Career**
- `jobs(id, title, org, location, url, source, retrieved_at, deadline, requirements_json, evidence_json, salary_json, status)`
- `applications(id, job_id, cv_variant_doc_id, status, submitted_at, follow_up_at, outcome)`
- `contacts(id, name_enc, org, role, notes_enc)` (sensitive) · `cv_variants(id, doc_id, target_job_id)`

**Knowledge**
- `memories` (§5) · `memory_vectors` (sqlite-vec virtual table) · `memories_fts` (FTS5)
- `knowledge_nodes`, `knowledge_edges` (§11) · `resources(id, kind, title, url, doc_id, skill_ids[], quality)`

**Agents & policy**
- `agents(id, name, kind, state, current_version, parent_agent_id, created_by, created_at, retired_at, retirement_reason)`
- `agent_manifests(agent_id, version, manifest_json, manifest_hash, created_at, created_by, diff_from_prev)`
- `agent_lifecycle_events(id, agent_id, from_state, to_state, reason, evidence_json, actor, approval_id, ts)`
- `agent_fitness(agent_id, version, window, component, value, n, ci_low, ci_high, computed_at)`
- `agent_runs` (doc 06 §49 fields) · `agent_steps(run_id, seq, kind (model|tool|message|checkpoint), payload_json, tokens_in, tokens_out, cost_eur, started_at, ended_at, status)`
- `agent_messages(id, from_agent, to_agent|channel, type, payload_json, correlation_id, ts)`
- `capabilities(id, parent, description, required_permissions[], risk_class, eval_suite_id)` · `agent_capabilities(agent_id, version, capability_id, depth)`
- `tools(id, version, kind (builtin|generated|integration), spec_json, code_ref, sandbox_profile, state, risk_class, tests_ref, approved_by)` · `skills(id, version, workflow_json, io_schema_json, state, tests_ref)`
- `grants(id, domain, action_class, level, conditions_json, granted_by, granted_at, expires_at, revoked_at)`
- `policy_decisions(id, run_id, action_class, inputs_json, scores_json, rule_matched, outcome, ts)`
- `approvals(id, action_id, requested_by, artifact_ref, summary, risk_json, alternatives_json, status, decided_by, decided_at, edit_diff)`
- `actions(id, run_id, intent, action_class, payload_json, expected_result, observed_result, status (verification statuses), verifier, verification_json, undo_ref)`
- `budgets(id, owner (agent|team|global), period, tokens_limit, eur_limit, tool_calls_limit, time_limit_s, used_json)`
- `org_learning(id, task_type, config_hash, manifest_ref, model, tools[], cost_eur, latency_s, verified, corrections, ts)`

**Infra**
- `jobs_queue(id, kind, payload_json, run_after, lease_until, attempts, max_attempts, last_error, status, idempotency_key)` · `dead_letters(id, source (job|event_handler), ref, error, payload_json, ts)` · `handler_ledger(event_id, handler, processed_at, status)`
- `audit_log(id, actor, action, target, detail_json, ts)` (append-only; hash-chained: `prev_hash`, `hash`)
- `users(id, display_name)` · `credentials(id, user_id, kind (passkey|password), data)` · `sessions_auth(id, user_id, token_hash, expires_at, device)`
- `integration_accounts(id, adapter, config_json, secret_ref, state, last_sync_at, last_status)`

## 5. Memory architecture (ADR-09)

| Type | Holds | Write path | Decay |
|---|---|---|---|
| Working | current task state, scratch | agent runtime (per run) | deleted at run end (summary may be promoted) |
| Episodic | what happened (events, runs, outcomes) with time | reflection jobs, run summaries | half-life 180 d unless referenced |
| Semantic | stable facts about you/world | consolidation from episodic, principal statements | none; reviewed via `review_at` |
| Procedural | how to do recurring tasks (playbooks) | Factory/skills from successful runs | versioned; superseded |
| Preference | your preferences/constraints | principal + inferred (confirmed) | none; confirm-on-infer |
| Strategic | goals rationale, key decisions | GoalService, Decision Journal | none |
| Failure | what didn't work + why | verification failures, experiments rejected | none |
| Success | what worked well + conditions | verified successes, experiments continued | none |
| Agent | per-agent learnings | agent runtime (scoped namespace) | archived with agent |

`memories(id, type, namespace, key, content (enc if sensitive), embedding_id, source, provenance_json
(event ids/run ids/urls), confidence, importance, created_at, last_accessed_at, review_at, decay_half_life_d,
valid_from, valid_to, supersedes_id, contradiction_of_id, status (active|superseded|retracted|archived))`

**Write pipeline (MemoryService.propose):**

1. Validate: type and namespace allowed for the caller's capability.
2. **Dedupe:** same key, or cosine > 0.92 within the namespace → merge (raise confidence, append provenance).
3. **Contradiction detection:** same subject with negated or incompatible content (NLI-style check by the fast model plus rule checks on typed facts):
   - the higher-confidence, more recent record with stronger provenance stays active,
   - the other becomes `contradiction_of`, and an inbox item asks you to resolve it if both are confident.
4. **Confidence:** source prior (principal 1.0, verified measurement 0.9, agent inference 0.5–0.7) × provenance count.
5. Store and index (FTS + vector).

**Consolidation job (nightly):** episodic → semantic/procedural/success/failure summaries. It prunes
low-importance, decayed episodic items (archived, not destroyed) and refreshes `review_at` on
stale semantic facts.

**Retrieval:**

```
score = 0.45·vector_sim + 0.25·bm25 + 0.15·importance + 0.10·recency + 0.05·confidence
```

Retrieval is filtered by the caller's `memory_scope`, has a token budget, and returns provenance with every item.

**Correction:** principal edit → new record (`source=principal`, confidence 1.0) supersedes; forget = hard delete including vectors (audit keeps the id only).

**ADR-09 (local embeddings).**
- **DECISION.** On-device embedding model (e.g. `bge-small` ONNX through onnxruntime-node) plus sqlite-vec.
- **RATIONALE.** Personal memories never leave the host just to be indexed.
- **ALTERNATIVES.** Hosted embeddings API: better quality, but a privacy cost.
- **TRADE-OFFS.** A few hundred MB of model and slower indexing; quality is adequate for personal-scale recall.

## 11. Knowledge graph schema (ADR-08)

```
knowledge_nodes(id, type, key UNIQUE(type,key), label, props_json, confidence, provenance_json,
                created_by, created_at, updated_at, status)
knowledge_edges(id, src_id, dst_id, type, props_json, weight, confidence, evidence_json,
                valid_from, valid_to, created_by, created_at)
```

**Node types (24):** Person (self), Goal, Skill, KnowledgeArea, Project, Task, Habit, Session, Metric, Event,
Resource, Weakness, Strength, Bottleneck, Opportunity, Decision, Experiment, Hypothesis, Intervention,
Outcome, Organization, Contact, Job, Document, Course, Exam.

**Edge types (typed signatures, validated):**

| Edge | From → To |
|---|---|
| REQUIRES | Goal/Job/Exam → Skill/KnowledgeArea |
| SUPPORTED_BY | Skill/KnowledgeArea → Session/Resource/Course |
| BLOCKS | Weakness/Bottleneck → Goal/Project/Skill |
| TARGETS | Intervention → Weakness |
| ADVANCES | Project/Task/Habit → Goal |
| SUPPORTS | Opportunity/Course/Resource → Goal/Skill |
| TESTS | Experiment → Hypothesis |
| PRODUCED | Experiment/Intervention/Decision → Outcome |
| MEASURED_BY | Skill/Goal/Habit → Metric |
| PART_OF | Goal → Goal, Task → Project, KnowledgeArea → KnowledgeArea |
| SYMPTOM_OF | Weakness/Bottleneck → Bottleneck |
| OFFERED_BY | Job/Course → Organization |
| KNOWS | Contact → Organization/Contact |
| EVIDENCE_FOR / AGAINST | Event/Outcome/Document → Hypothesis/Weakness/Strength |
| HAS_STRENGTH / HAS_WEAKNESS | Person → Strength/Weakness |

Nodes are mirrored from domain rows by handlers (`goal.created` → Goal node). Edges come from domain
logic or from agents (`kg:write:ns=…`). Agent-created edges carry confidence < 1 and evidence.

**Query API:** typed traversals, not raw SQL for agents:

```
kg.neighbors(nodeId, {edgeTypes, direction, depth≤3})
kg.path(from, to, {edgeTypes, maxDepth})
kg.query(GraphPattern)  // small pattern language: (Goal {status:active})<-[BLOCKS]-(Weakness {severity>0.6})
```

Agents call these through a `kg_query` tool. Implementation: recursive CTEs over the edges table, indexed `(src_id,type)`, `(dst_id,type)`.

**ADR-08.**
- **DECISION.** The graph lives in SQLite.
- **RATIONALE.** Personal scale (≤ 10⁶ edges), one store to back up, transactional with domain writes.
- **ALTERNATIVES.** Neo4j/Memgraph (ops and sync cost), RDF triple stores (heavy).
- **TRADE-OFFS.** Deep traversals are slower; capped at depth 3, with path results cached in projections.

## 15. Analytics architecture

### Metric registry (ADR-07)

Each metric = **NAME · DEFINITION · FORMULA (pure TS fn ref) · UNIT · SOURCE (event types) · VERSION ·
CONFIDENCE METHOD · MIN_N**. Changing a formula creates a new version. History is recomputed into new rows,
and both versions stay queryable. `inputs_hash` (hash of the source event ids plus the version) makes any
value reproducible ("show me exactly which events produced this number").

**Evidence policy (§45).** A score without definition, formula, n and uncertainty is not displayed. If
`n < min_n`, it shows `UNKNOWN (need X more …)`. Composite indices always display their components and
weights.

### Recompute DAG

`event → affected metrics (by source types) → dependent metrics/engines → projections`. It is
incremental per period, with debounced jobs. Full rebuild is a command for formula version bumps.

### Key metric definitions (v1)

| Metric | Formula | n / uncertainty |
|---|---|---|
| Verified learning per hour (domain) | Δ mastery (assessment-based, same scale) / study hours in window | ≥ 2 assessments; bootstrap CI |
| Retained learning per hour | Σ items with p_recall(t+30d) ≥ 0.8 newly reached / hours | item model; CI by bootstrap over items |
| Retention@k (k = 1, 7, 30, 90, 180 d) | accuracy on items first learned ≥ k days ago, reviewed at lag ≈ k (±20%) | Wilson interval; n per lag |
| Item half-life | half-life regression: p = 2^(−Δ/h), log2 h = θ·features (lag, reviews, lapses, difficulty, domain) | fitted per user; refit weekly |
| Mastery (law area) | Beta(α=1+correct_w, β=1+wrong_w), recency-weighted (half-life 30 d) → posterior mean + 90% interval | interval shown |
| Readiness (exam) | P(score ≥ pass) by Monte Carlo over area posteriors × exam blueprint weights | probability + interval |
| CEFR estimate (per skill) | ordinal model on assessment evidence mapped to CEFR descriptors (evidence matrix); the estimate is the highest level with ≥ 2 independent pieces of evidence at ≥ 70% | shows evidence count per level |
| Active share | active minutes / total minutes | n days |
| Deep Work Quality (DWQ) | `uninterrupted_ratio × focus/5 × output_factor × complexity_weight`, where output_factor ∈ {0, 0.5, 1} from task completion/quality; components shown | per block; daily mean with n |
| Plan accuracy | 1 − |planned − actual| / planned (per task, capped) | n tasks |
| Priority completion | completed top-3 / top-3 planned | n days |
| Phone vs target | phone_min / target (target configurable, e.g. 60) | daily |
| Sustainability index | weighted components (sleep regularity and duration vs need, subjective energy trend, workload vs capacity, consistency, overload indicators). Components shown; < 2 components = UNKNOWN | per component n |
| Progress velocity (domain) | slope of verified outcome metric (mastery/CEFR score/pipeline stage value) per week, robust (Theil–Sen) with CI; also per 100 h | needs ≥ 4 points |
| APEX velocity | goal-weighted mean of domain velocities normalized by each goal's required rate (on track = 1.0) | components shown |

### Causality ladder (§46)

`OBSERVATION → CORRELATION → HYPOTHESIS → EXPERIMENTAL_EVIDENCE → LIKELY_CAUSAL → STRONG_CAUSAL`.

| Level | Required |
|---|---|
| CORRELATION | n ≥ 14 paired days, \|ρ\| ≥ 0.3, permutation p < 0.05 |
| EXPERIMENTAL_EVIDENCE | one randomized N-of-1 experiment, effect CI excludes 0 |
| LIKELY_CAUSAL | ≥ 2 concordant experiments, or 1 experiment plus a mechanism plus a dose-response |
| STRONG_CAUSAL | replicated ≥ 3×, effect stable over ≥ 60 days, no alternative explanation flagged by the Critic |

UI and agent outputs must carry the label. The Critic rejects text that over-claims.

### Weakness Engine (§16)

```
weakness = {domain, category (taxonomy), evidence[], confidence, severity, recurrence, impact, trend,
            suspected_cause, recommended_intervention}
severity    = 1 − mastery (or error rate) on the weakness scope
recurrence  = occurrences in last 30 d / (exposures in last 30 d)
impact      = Σ over goals: goal_weight × blueprint/required weight of the scope
confidence  = from posterior width / evidence count
priority    = impact × severity × recurrence × confidence / expected_cost_hours
```

**Law error diagnosis (§14):** each wrong answer is classified as
- `KNOWLEDGE_GAP`: item never correctly answered, concept unseen,
- `MEMORY_GAP`: was correct before, decayed, long lag,
- `MISREADING`: correct when rephrased, or the self-report says misread,
- `REASONING_ERROR`: rule known, but wrong application or syllogism step,
- `CARELESS`: fast answer and high item mastery.

Classification combines rules (history, timing, mastery) with the fast model on the explanation text
when available. It is shown as a distribution, not as certainty.

### Bottleneck Engine (§17)

1. Candidates come from every domain's constraints: knowledge, skill, sleep/recovery, attention, execution, planning, environment, information, strategy.
2. For each candidate, estimate the **marginal return** of relieving it: expected Δ goal-weighted velocity per hour, from forecasts and org-learning priors.
3. **Symptom vs cause.** Walk `SYMPTOM_OF`/`BLOCKS` edges and check temporal precedence plus lagged correlation; for example, "low law accuracy" ← "low retention" ← "evening sessions after poor sleep". The engine names the deepest node with sufficient evidence as the **root-cause candidate**, with its evidence level.
4. Output: one current system bottleneck + top 2 alternatives, each with evidence and confidence.

### Priority Engine (§26), max TOP 3

```
EV(action)  = impact_on_goal_velocity × goal_weight × P(success) × urgency_factor(deadline)
cost(action)= time_h × energy_cost(sustainability band) + € cost
priority    = EV / cost, constrained by daily capacity (sustainability index) and diversity (≤2 per domain)
```

Each priority carries **WHY NOW · EXPECTED IMPACT · EVIDENCE · NEXT ACTION · TIME COST · CONFIDENCE**.
The Coach format (§27) is enforced in the schema: `fact` (data, with ids), `interpretation` (labeled with
its evidence level) and `action` (concrete) are separate fields and are never merged.

### Forecasting (§28)

| Target | Method | Output |
|---|---|---|
| German CEFR date | growth model on assessment scores vs cumulative active hours (log-linear), with hours/week scenarios | base, upside and downside dates, plus assumptions (hours/week, active share) |
| Law exam readiness | Monte Carlo over mastery posteriors × study plan → P(pass) on the exam date | probability band |
| Goal completion | trajectory extrapolation (Theil–Sen) with residual bootstrap | P(on time), date quantiles |
| Skill development | per-skill growth curve with saturation | level by date |

Each forecast stores its assumptions, evidence and model version. It is **re-forecast** on new assessments, and
calibration (forecast vs actual) is tracked as a metric of APEX itself.

### Data Explorer and natural-language querying (ADR-20)

- UI: filters, date ranges, comparisons, cohorts, correlations, trends, distributions and raw-event inspection.
- NL query → the `standard` LLM compiles it into a **constrained AnalysisPlan DSL**:

  ```ts
  { dataset: "metric" | "events" | "sessions",
    select: [...], filters: [...], groupBy, window, lag?,
    test: "correlation" | "difference" | "trend" | "distribution",
    controls?: [...] }
  ```

- The plan is validated and executed deterministically by `AnalyticsService`.
- The response renders **methodology, sample (n, dates), limitations and causality level**.
- The LLM may then *explain* the computed result, but it never produces the numbers.

Example: *"Does phone use predict lower German performance the following day?"* →
`{select: [phone_min(d), german_accuracy(d+1)], lag: 1, test: "correlation", controls: ["sleep_min(d)"]}`
→ partial Spearman ρ, n, permutation p and CI → "CORRELATION (not causal). Test it with experiment template X."

### ADR-20

- **DECISION.** NL queries become a DSL that executes deterministically.
- **RATIONALE.** No pseudo-analytics, reproducibility, testability.
- **ALTERNATIVES.** LLM-written SQL (injection and hallucination risk) or LLM-computed answers (invented numbers).
- **TRADE-OFFS.** The DSL limits expressiveness at first; it is extended deliberately.
