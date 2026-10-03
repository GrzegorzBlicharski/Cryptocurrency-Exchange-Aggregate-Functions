# 06 — API Contracts & Core TypeScript Interfaces

## ADR-22 API style

- **DECISION.**
  - The UI reads through React Server Components calling query services directly (same process), and mutates through Server Actions that call the same command handlers as the REST API.
  - The **stable public contract** is REST `/api/v1`, with zod schemas in `packages/contracts` generating TS types and OpenAPI 3.1.
  - Live agent and job progress streams over **SSE**.
- **RATIONALE.** One schema source; fast server-side rendering; a stable API for the PWA, automations, MCP and future clients.
- **ALTERNATIVES.**
  - tRPC: excellent DX, but TS-only clients and a less standard contract.
  - GraphQL: flexible, but heavier and needs caching complexity.
- **TRADE-OFFS.** Some duplication between server actions and REST handlers, mitigated by both delegating to command handlers.

## 18. API contracts (`/api/v1`, JSON, auth: session cookie or scoped bearer token)

Conventions:
- `Idempotency-Key` header on POST.
- Cursor pagination (`?cursor=&limit=`).
- Errors: RFC 9457 `application/problem+json`.
- Every write returns `{id, correlation_id}`.
- Every computed value carries `{value, unit, n, ci, status, metric_version, as_of}`.

| Area | Endpoints |
|---|---|
| Events | `POST /events` (single or batch, device tokens allowed) · `GET /events?type=&domain=&from=&to=` · `POST /events/{id}/correct` · `POST /events/{id}/retract` |
| Quick capture | `POST /capture` (natural-language or structured quick log → proposed events → confirm) |
| Metrics | `GET /metrics/definitions` · `GET /metrics/{id}/values?scope=&from=&to=` · `GET /metrics/{id}/explain?period=` (inputs, formula, version) |
| Mission Control | `GET /mission-control` (projection: today, top3, velocity, bottleneck, weakness, opportunity, ai_actions, approvals, goals_at_risk, wins, sustainability, forecast) |
| Goals | `GET/POST /goals` · `PATCH /goals/{id}` · `GET /goals/tree` · `GET /goals/health` (drift, orphans, conflicts, neglect, over/under-investment) |
| Projects / tasks | `GET/POST /projects` · `GET/POST /tasks` · `PATCH /tasks/{id}` (status, actuals) · `GET /plan/today` |
| Domains | `GET /domains` · `GET /domains/{d}/command-center` · `GET /domains/{d}/weaknesses` · `GET /domains/{d}/forecast` |
| Engines | `GET /bottleneck` · `GET /weaknesses?domain=` · `GET /priorities` (≤3) · `GET /recommendations?status=` · `POST /recommendations/{id}/{accept|dismiss|feedback}` |
| Experiments | `GET/POST /experiments` · `POST /experiments/{id}/{start|evaluate|stop}` |
| Forecasts | `GET /forecasts?target=` · `POST /forecasts/recompute` |
| Decisions | `GET/POST /decisions` · `PATCH /decisions/{id}` (outcome) · `GET /decisions/calibration` |
| Analytics | `POST /analysis/plan` (NL → AnalysisPlan, no execution) · `POST /analysis/run` (AnalysisPlan → result with methodology) · `GET /explorer/events` |
| Memory | `GET /memories?type=&q=` · `POST /memories` · `POST /memories/{id}/correct` · `DELETE /memories/{id}` · `GET /operating-manual` |
| Knowledge graph | `GET /kg/nodes/{id}` · `GET /kg/neighbors/{id}` · `POST /kg/query` (GraphPattern) |
| Inbox | `GET /inbox?class=` · `POST /inbox/{id}/{read|done|dismiss|rate}` |
| Approvals | `GET /approvals?status=pending` · `POST /approvals/{id}/{approve|reject|edit-approve}` |
| Agents | `GET /agents` · `GET /agents/{id}` · `GET /agents/{id}/why` · `GET /agents/{id}/fitness` · `POST /agents/{id}/{pause|resume|retire}` · `GET /capabilities` · `GET /capabilities/{id}/who-can` |
| Agent Factory | `GET /factory/gaps` · `GET /factory/proposals` · `POST /factory/proposals/{id}/{approve|reject}` · `GET /factory/evaluations/{id}` |
| Runs | `GET /runs?agent=&status=` · `GET /runs/{id}` (steps, actions, verification, cost) · `POST /runs` (manual trigger) · `GET /stream/runs/{id}` (SSE) |
| Tools / skills | `GET /tools` · `GET /tools/{id}` · `GET /skills` · `POST /tools/{id}/{approve|deprecate}` |
| Policy | `GET /policy/grants` · `POST /policy/grants` · `DELETE /policy/grants/{id}` · `GET /policy/thresholds` · `PATCH /policy/thresholds` (bounded) · `POST /policy/kill-switch` |
| Budgets | `GET /budgets` · `PATCH /budgets/{id}` |
| Integrations | `GET /integrations` · `POST /integrations/{id}/configure` · `POST /integrations/{id}/sync` · `GET /integrations/{id}/health` |
| Observability | `GET /observability/ai?window=` · `GET /audit?actor=&action=` · `GET /audit/verify` |
| Data | `GET /export?format=json|csv|ndjson|sqlite` · `POST /wipe` (re-auth) |

**SSE stream events:** `run.step`, `run.action`, `run.approval_requested`, `run.completed`, `job.progress`, `inbox.new`.

## 19. Core TypeScript interfaces (`packages/contracts`, zod-backed; abbreviated)

```ts
// ---------- primitives
export type ULID = string; export type ISODate = string; export type ISODateTime = string;
export type Domain = string;                        // registry-defined, e.g. "german" | "law" | …
export type DataClass = "normal" | "personal" | "health" | "finance" | "identity";
export type EvidenceLevel = "OBSERVATION" | "CORRELATION" | "HYPOTHESIS" | "EXPERIMENTAL_EVIDENCE"
  | "LIKELY_CAUSAL" | "STRONG_CAUSAL";
export type Tier = "fast" | "standard" | "deep" | "critical" | "max" | "vision" | "local";
export type AutonomyLevel = 0 | 1 | 2 | 3 | 4 | 5;
export type RiskClass = "low" | "medium" | "high";
export type Capability = string;                    // e.g. "web_research", "legal_analysis.contract"
export type Permission = string;                    // e.g. "data.german:read", "web:fetch"

export interface Measured<T = number> {
  value: T | null; unit: string; n: number; ci?: [number, number];
  status: "ok" | "unknown" | "insufficient" | "stale"; need?: string;
  metricId: string; metricVersion: number; asOf: ISODateTime; inputsHash?: string;
}

// ---------- events
export interface ApexEvent<P = unknown> {
  id: ULID; ts: ISODateTime; recordedAt: ISODateTime; type: string; typeVersion: number;
  domain: Domain; source: string; subjectId?: string; durationS?: number; value?: number; unit?: string;
  payload: P; confidence: number; idempotencyKey: string; correlationId?: string;
  supersedesId?: ULID; sensitivity: DataClass;
}

// ---------- metrics
export interface MetricDefinition {
  id: string; name: string; domain: Domain; definition: string; formulaRef: string; unit: string;
  direction: "up_good" | "down_good" | "target"; sourceEventTypes: string[]; version: number;
  minN: number; confidenceMethod: "wilson" | "bootstrap" | "posterior" | "none";
}

// ---------- goals, weaknesses, bottlenecks, priorities
export type GoalLevel = "life" | "outcome_3_5y" | "annual" | "quarterly" | "monthly" | "weekly" | "daily";
export interface Goal { id: ULID; parentId?: ULID; level: GoalLevel; title: string; why?: string;
  domain?: Domain; metricId?: string; target?: number; targetDate?: ISODate; weight: 1|2|3|4|5;
  status: "active" | "paused" | "done" | "dropped"; }
export interface Weakness { id: ULID; domain: Domain; category: string; taxonomyCode?: string;
  evidence: EvidenceRef[]; confidence: number; severity: number; recurrence: number; impact: number;
  trend: "improving" | "stable" | "worsening" | "unknown"; suspectedCause?: string;
  recommendedInterventionId?: ULID; priority: { value: number; components: Record<string, number> }; }
export interface Bottleneck { id: ULID; kind: "knowledge"|"skill"|"recovery"|"attention"|"execution"
  |"planning"|"environment"|"information"|"strategy"; scope: string; evidence: EvidenceRef[];
  symptomOf?: ULID; rootCauseConfidence: number; marginalReturnPerHour: Measured; }
export interface EvidenceRef { kind: "event" | "metric" | "assessment" | "url" | "run" | "document";
  ref: string; note?: string; }

export interface Recommendation {               // Coach format: never mix fact and interpretation
  id: ULID; fact: { statement: string; evidence: EvidenceRef[] };
  interpretation: { statement: string; evidenceLevel: EvidenceLevel; confidence: number };
  action: { title: string; nextStep: string; timeCostMin: number; domain: Domain };
  whyNow: string; expectedImpact: string; priority: { value: number; components: Record<string, number> };
}

// ---------- experiments, forecasts, decisions
export interface Experiment { id: ULID; hypothesis: string; baseline: { from: ISODate; to: ISODate };
  interventionId: ULID; design: "ab_days" | "abab" | "pre_post"; schedule: ISODate[];
  metrics: string[]; minN: number; status: "planned"|"baseline"|"running"|"evaluated"|"stopped";
  result?: { effect: number; ci: [number, number]; p?: number; evidenceLevel: EvidenceLevel };
  decision?: "continue" | "modify" | "reject"; }
export interface Forecast { id: ULID; target: { kind: "goal"|"metric"|"exam"|"skill"; id: string };
  method: string; horizon: ISODate; base: number | ISODate; upside: number | ISODate; downside: number | ISODate;
  assumptions: Record<string, unknown>; evidence: EvidenceRef[]; confidence: number; modelVersion: string; }
export interface DecisionRecord { id: ULID; title: string; context: string; options: string[];
  assumptions: string[]; expectedOutcome: string; confidence: number; risks: string[];
  finalChoice?: string; decidedAt?: ISODateTime; reviewAt?: ISODate; actualOutcome?: string;
  calibrationError?: number; }

// ---------- memory & KG
export type MemoryType = "working"|"episodic"|"semantic"|"procedural"|"preference"|"strategic"
  |"failure"|"success"|"agent";
export interface MemoryRecord { id: ULID; type: MemoryType; namespace: string; key?: string; content: string;
  source: string; provenance: EvidenceRef[]; confidence: number; importance: number;
  createdAt: ISODateTime; reviewAt?: ISODate; decayHalfLifeD?: number; validFrom?: ISODateTime;
  validTo?: ISODateTime; supersedesId?: ULID; contradictionOfId?: ULID;
  status: "active"|"superseded"|"retracted"|"archived"; }
export interface KGNode { id: ULID; type: string; key: string; label: string; props: Record<string, unknown>;
  confidence: number; provenance: EvidenceRef[]; }
export interface KGEdge { id: ULID; src: ULID; dst: ULID; type: string; weight?: number; confidence: number;
  evidence: EvidenceRef[]; validFrom?: ISODateTime; validTo?: ISODateTime; }

// ---------- agents (AgentManifest = §2 of the Agent Factory brief)
export interface AgentManifest {
  id: string; name: string; version: number; kind: "persistent" | "temporary" | "team_lead";
  mission: string; description: string;
  createdBy: string; createdAt: ISODateTime; creationReason: { gapId?: ULID; summary: string };
  capabilities: Capability[]; limitations: string[];
  inputSchema: JSONSchema; outputSchema: JSONSchema;
  allowedTools: string[]; prohibitedTools: string[]; requiredPermissions: Permission[];
  memoryScope: { read: string[]; write: string[] };            // namespaces
  contextSources: Array<{ kind: "domain" | "metric" | "kg" | "memory" | "documents"; ref: string }>;
  preferredModel: Tier | string; fallbackModels: Array<Tier | string>;
  limits: { maxRuntimeS: number; maxCostEur: number; maxToolCalls: number; maxTokens: number };
  autonomy: Array<{ domain: Domain; actionClass: string; level: AutonomyLevel }>; // ≤ grants
  riskClass: RiskClass;
  successCriteria: string[]; verificationMethod: VerifierKind[];
  escalationRules: Array<{ when: string; to: "principal" | "parent" | "chief_of_staff"; via: "inbox" | "approval" }>;
  parentAgent?: string; childAgentsAllowed: boolean; maxChildren?: number;
  expirationPolicy: { kind: "none" | "ttl" | "on_project_done" | "on_budget_exhausted"; ttlDays?: number; projectId?: ULID };
  promptTemplateId: string; promptParams: Record<string, string>;
  evalSuiteIds: string[];
}
export type AgentState = "draft"|"compiled"|"sandbox"|"limited"|"production"|"paused"
  |"rejected"|"retired"|"merged"|"archived";
export interface FitnessVector { window: string; components: Record<
  "task_success"|"verified_success"|"error"|"human_correction"|"escalation_quality"|"cost_per_success"
  |"latency_p50"|"latency_p95"|"tool_efficiency"|"hallucination"|"reusability",
  { value: number | null; n: number; ci?: [number, number] }>; }

// ---------- agent runs (§49)
export interface AgentRun {
  id: ULID; agentId: string; agentVersion: number; agentType: string;
  trigger: { kind: "schedule" | "event" | "principal" | "agent"; ref: string };
  goalId?: ULID; projectId?: ULID; inputContext: { sources: EvidenceRef[]; tokens: number };
  plan: Array<{ step: string; status: "todo" | "doing" | "done" | "blocked" }>;
  model: { requested: Tier | string; served: string[] };
  tools: string[]; actions: ULID[]; observations: Array<{ seq: number; summary: string; ref?: string }>;
  result?: { status: "success" | "partial" | "failed" | "needs_human"; summary: string; artifacts: EvidenceRef[] };
  verification?: VerificationRecord; confidence?: number;
  cost: { tokensIn: number; tokensOut: number; cacheRead: number; eur: number };
  durationMs: number; errors: Array<{ seq: number; kind: string; message: string }>;
  humanApprovals: ULID[]; memoryUpdates: ULID[]; checkpointSeq: number;
  status: "queued" | "running" | "suspended" | "completed" | "failed" | "cancelled";
}
export interface AgentMessage { id: ULID; from: string; to: string; type: "task" | "result" | "question"
  | "status" | "capability_request"; payload: unknown; correlationId: ULID; ts: ISODateTime; }

// ---------- policy, approval, verification
export interface ProposedAction { id: ULID; runId: ULID; agentId: string; actionClass: string; domain: Domain;
  intent: string; payload: unknown; expectedResult: string; reversible: boolean; undo?: string;
  impact: number; confidence: number; costEur: number; dataClasses: DataClass[]; }
export interface PolicyDecision { actionId: ULID; outcome: "allow" | "requires_approval" | "deny";
  ruleMatched: string; scores: { risk: number; impact: number; reversibility: number; confidence: number };
  inputs: Record<string, unknown>; }
export interface ApprovalRequest { id: ULID; actionId: ULID; summary: string; artifactRef?: string;
  risk: PolicyDecision["scores"]; alternatives: string[]; deadline?: ISODateTime;
  status: "pending" | "approved" | "rejected" | "edited_approved" | "expired"; }
export type VerifierKind = "deterministic" | "schema" | "provenance" | "cross_check" | "critic" | "outcome" | "human";
export type VerificationStatus = "NOT_STARTED"|"IN_PROGRESS"|"ACTION_EXECUTED"|"VERIFICATION_PENDING"
  |"VERIFIED_SUCCESS"|"PARTIAL_SUCCESS"|"FAILED"|"NEEDS_HUMAN";
export interface VerificationRecord { actionId?: ULID; runId?: ULID; intent: string; expected: string;
  observed?: string; verifiers: VerifierKind[]; status: VerificationStatus; evidence: EvidenceRef[]; }

// ---------- tools, skills, models, integrations: see doc 04 for ToolSpec / SkillSpec / IntegrationAdapter
export interface ModelRoute { tier: Tier; model: string; provider: string; effort?: string;
  reason: string; fallbacks: string[]; estCostEur: number; }
export interface LLMProvider { id: string; models(): ModelInfo[];
  generate(req: LLMRequest, opts: { signal: AbortSignal; budget: BudgetHandle }): Promise<LLMResponse>;
  stream?(req: LLMRequest, opts: { signal: AbortSignal; budget: BudgetHandle }): AsyncIterable<LLMStreamEvent>; }

// ---------- pluggable domains
export interface DomainModule { id: Domain; label: string; eventTypes: EventTypeDef[];
  metrics: MetricDefinition[]; weaknessTaxonomy: TaxonomyNode[]; forecastModels: string[];
  analystManifest: AgentManifest; commandCenter: { route: string; panels: string[] };
  projections: ProjectionDef[]; }
```

### Service interfaces (§48)

```ts
export interface EventService { append(e: NewEvent | NewEvent[], ctx: Ctx): Promise<ULID[]>;
  correct(id: ULID, patch: unknown, ctx: Ctx): Promise<ULID>; retract(id: ULID, reason: string, ctx: Ctx): Promise<void>;
  query(q: EventQuery, scope: Scope): Promise<Page<ApexEvent>>; }
export interface MetricService { definitions(domain?: Domain): MetricDefinition[];
  value(metricId: string, period: Period, scope: Scope): Promise<Measured>;
  series(metricId: string, range: DateRange, scope: Scope): Promise<Measured[]>;
  explain(metricId: string, period: Period): Promise<{ formula: string; inputs: EvidenceRef[]; version: number }>;
  recompute(metricId: string, range?: DateRange): Promise<JobRef>; }
export interface GoalService { tree(): Promise<GoalNode[]>; create(g: NewGoal, ctx: Ctx): Promise<Goal>;
  health(): Promise<{ drift: Finding[]; orphans: Finding[]; conflicts: Finding[]; neglected: Finding[];
    overInvested: Finding[]; underInvested: Finding[] }>; link(activity: EvidenceRef, goalId: ULID): Promise<void>; }
export interface AnalyticsService { compile(nl: string): Promise<AnalysisPlan>;
  run(plan: AnalysisPlan, scope: Scope): Promise<AnalysisResult>;       // deterministic
  correlate(x: SeriesRef, y: SeriesRef, opts: { lag?: number; controls?: SeriesRef[] }): Promise<AnalysisResult>; }
export interface MemoryService { propose(m: NewMemory, ctx: Ctx): Promise<MemoryWriteResult>;
  retrieve(q: string, scope: MemoryScope, budgetTokens: number): Promise<MemoryRecord[]>;
  correct(id: ULID, content: string, ctx: Ctx): Promise<MemoryRecord>; forget(id: ULID, ctx: Ctx): Promise<void>;
  consolidate(): Promise<JobRef>; }
export interface AgentService { registry: AgentRegistry; factory: AgentFactory; orchestrator: Orchestrator;
  run(agentId: string, trigger: AgentRun["trigger"], input: unknown): Promise<AgentRun>;
  resume(runId: ULID): Promise<AgentRun>; cancel(runId: ULID): Promise<void>; }
export interface AgentRegistry { get(id: string, version?: number): Promise<AgentManifest>;
  whoCan(cap: Capability, c?: Constraints): Promise<Array<{ agentId: string; fit: number; fitness: FitnessVector; costPerSuccess: number }>>;
  why(id: string): Promise<AgentWhy>; }
export interface AgentFactory { detectGaps(): Promise<CapabilityGap[]>;
  reuseLadder(gap: CapabilityGap): Promise<ReuseDecision>;
  design(gap: CapabilityGap): Promise<AgentManifest>; compile(m: AgentManifest): Promise<CompiledAgent>;
  evaluate(agentId: string, version: number): Promise<EvaluationReport>;
  transition(agentId: string, to: AgentState, reason: string, ctx: Ctx): Promise<void>;
  proposeMerge(a: string, b: string): Promise<MergeProposal | null>; retire(agentId: string, reason: string): Promise<RetirementReport>; }
export interface ToolService { list(scope?: Scope): ToolSpec[];
  invoke(token: CapabilityToken, toolId: string, input: unknown): Promise<ToolResult>;
  propose(spec: ToolSpec, code: string, ctx: Ctx): Promise<ToolProposal>; }
export interface ModelRouter { route(task: RoutableTask): ModelRoute; record(outcome: RouteOutcome): Promise<void>; }
export interface PolicyService { evaluate(a: ProposedAction, token: CapabilityToken): Promise<PolicyDecision>;
  grants(): Promise<Grant[]>; setGrant(g: NewGrant, ctx: PrincipalCtx): Promise<Grant>; killSwitch(scope: Domain | "all"): Promise<void>; }
export interface ApprovalService { request(a: ProposedAction, d: PolicyDecision): Promise<ApprovalRequest>;
  decide(id: ULID, decision: "approve" | "reject" | "edit_approve", edit?: unknown, ctx?: PrincipalCtx): Promise<void>; }
export interface VerificationService { expect(actionId: ULID, expected: string, verifiers: VerifierKind[]): Promise<void>;
  verify(actionId: ULID): Promise<VerificationRecord>; }
export interface ExperimentService { create(e: NewExperiment): Promise<Experiment>; evaluate(id: ULID): Promise<Experiment>; }
export interface ForecastService { forecast(target: Forecast["target"]): Promise<Forecast>; calibration(): Promise<Measured>; }
export interface RecommendationService { priorities(): Promise<Recommendation[]>;   // ≤ 3
  feedback(id: ULID, f: "useful" | "not_useful" | "wrong", note?: string): Promise<void>; }
export interface NotificationService { consider(item: InboxCandidate): Promise<"interrupt" | "digest" | "drop">;
  inbox(cls?: "CRITICAL" | "IMPORTANT" | "FYI"): Promise<InboxItem[]>; }
export interface IntegrationService { adapters(): IntegrationAdapter<unknown, unknown>[];
  sync(id: string): Promise<SyncResult>; health(id: string): Promise<HealthStatus>; }
```
