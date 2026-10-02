# APEX OS — Architecture

> Objective function: **maximize verified long-term progress, healthy functioning,
> capability and life quality per sustainable hour of effort.**
> Not hours, not streaks, not task counts, not pressure.

APEX is a closed loop, not a habit tracker:

```
OBSERVE → MEASURE → ANALYZE → DETECT → DIAGNOSE → PRIORITIZE → RECOMMEND
        → ACT WHEN AUTHORIZED → MEASURE RESULT → LEARN → ADAPT → REPEAT
```

Every daily cycle answers five questions:

1. Where am I now? → domain status cards + APEX score
2. What is holding me back? → bottleneck (lowest-scoring, highest-weight domain) + warnings
3. What matters most now? → today's #1 priority (Priority Engine + Orchestrator)
4. What is the highest-value next action? → Next Best Actions queue
5. Is the strategy actually working? → Progress Velocity, experiments, weekly/monthly reviews

---

## 1. System architecture

### Stack (chosen for low maintenance and Codex-friendliness)

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11 + FastAPI | typed, small, very well known by code agents |
| DB | SQLite (WAL) via SQLAlchemy 2.0 ORM | zero-ops single-user; same models run on PostgreSQL by changing `APEX_DATABASE_URL` |
| Frontend | Server-rendered Jinja2 + one CSS file + ~50 lines of vanilla JS | no build step; fast on Chromebook and phone; "WHY?" uses native `<details>` |
| API | JSON REST under `/api/v1` (same services as the HTML UI) | lets future mobile/agent clients reuse everything |
| Agent runtime | in-process deterministic agents + Orchestrator | works with **zero** external APIs; LLM adapters are optional add-ons |
| Scheduler | in-process asyncio loop + `job_runs` table (idempotent per period) | brief/review jobs survive restarts without double-running |
| Event system | append-only `events` table + in-process bus | audit trail and invalidation trigger for the orchestrator |
| Auth | single owner, scrypt password hash, random session tokens (HttpOnly, SameSite=Strict cookie or Bearer) | stdlib crypto only |
| Encryption | Fernet field-level encryption for free-text sensitive columns | protects notes/health text in the DB file and in backups |

### Module map

```
apex/
  config.py           settings (env / .env), timezone, budgets
  db.py               engine, session, init
  models.py           all tables (§3)
  crypto.py           EncryptedText column type, key management
  security.py         password hashing, sessions, auth dependencies
  audit.py            audit log + event bus
  memory.py           layered memory service (§4)
  autonomy.py         autonomy levels, policy, approval gates (§5)
  untrusted.py        external-content quarantine (prompt-injection defense)
  engines/
    stats.py          robust stats, trend, correlation with confidence labels
    sustainability.py Sustainability Index
    priority.py       Priority Engine (multi-criteria scoring)
    velocity.py       Progress Velocity
    insights.py       Insight objects with CAUSALITY GUARDRAIL labels
    notifications.py  notification budget / anti-nagging
    experiments.py    experiment engine
  agents/             one module per subagent + orchestrator (§2)
  reviews.py          morning brief, evening review, weekly & monthly reviews
  scheduler.py        periodic jobs
  integrations/       modular adapters with honest status (§6)
  api.py              JSON API
  web.py              HTML UI routes
```

### Request / cycle flow

```
user logs data ──► events(data.logged) ──► orchestrator marks state dirty
                                              │
dashboard / brief / scheduler ──► Orchestrator.run_cycle(date)
   1. build scoped AgentContext per agent (least privilege)
   2. agent.assess() → DomainStatus + Signals + CandidateActions
   3. Sustainability Index → capacity budget (minutes, cognitive load)
   4. Priority Engine scores every CandidateAction
   5. conflict resolution under budget → Next Best Actions + "deferred because"
   6. proactive triage of signals → IGNORE/LOG/INFORM/RECOMMEND/PREPARE/REQUEST_APPROVAL
   7. notification budget decides interrupt vs digest
   8. persist recommendations, inbox items, agent_actions, decision memory
```

---

## 2. Agent architecture

Every agent implements the same contract (`agents/base.py`):

```python
class Agent:
    name: str
    scopes: frozenset[str]          # tables it may read — enforced by AgentContext
    def assess(self, ctx) -> AgentReport
        # DomainStatus(score 0-100 | None, trend, headline, so_what)
        # signals: list[Signal(kind, severity, title, insight: Insight, deadline?)]
        # candidates: list[CandidateAction(...priority criteria..., why: Why)]
```

Agents **never** write to the DB and never call integrations directly; they only
return proposals. The Orchestrator is the single writer. This makes runaway agents
structurally impossible in the core loop and makes every agent unit-testable.

| Agent | Scopes | Key outputs |
|---|---|---|
| **German Intelligence** | learning_sessions, language_sessions, tests, questions, errors, goals | per-skill competency (speaking/writing/reading/listening/vocab/grammar), active-vs-passive ratio, weakest skill → targeted training, recurring errors, velocity per 100h |
| **Law Development** | learning_sessions, law_sessions, tests, questions, errors, goals | **Law Knowledge Graph**: area → STRONG / DEVELOPING / WEAK / CRITICAL GAP with n, accuracy, recency, retention after gap, practical-skill coverage (drafting, contract analysis, argumentation, research, case analysis) |
| **Career Intelligence** | career_opportunities, applications, skills, goals | match score, skill gaps, WHY IT MATTERS / MATCH / SKILL GAP / SALARY (only with source) / REQUIRED ACTION / DEADLINE; high-signal filter; prepares (never sends) CV tailoring plans |
| **LinkedIn Intelligence** *(phase 3)* | skills, career_opportunities, linkedin snapshot | keyword gap vs market; proposals only — publishing is a forbidden auto-action |
| **Learning Intelligence** | learning_sessions, sleep, movement, deep_work | HOW I LEARN BEST: time of day, session length, method; all as HYPOTHESIS/CORRELATION with confidence; feeds the Digital Twin |
| **Attention & Digital Hygiene** | screen_time, deep_work | phone/social trend, correlation phone ↔ deep work, interruptions, context switching |
| **Fitness & Movement** | movement, workouts | steps vs target, sedentary time, days since activity, acute:chronic load ratio (spike guard) |
| **Health & Recovery** | sleep, energy, recovery | sleep duration/regularity/debt, energy trend; **DETECT → FLAG → EXPLAIN → RECOMMEND NEXT STEP**, never diagnoses |
| **Productivity & Execution** | plan_items, deep_work | completion rate, planned vs actual, carry-over, execution latency, deep-work quality |
| **Research & Opportunity / Radar** *(phase 2)* | research_items | RELEVANCE, IMPACT, TIME COST, MONEY COST, EVIDENCE QUALITY, ACTIONABILITY → expected value gate |
| **APEX Orchestrator** | all reports (not raw data) + goals + sustainability | resolves conflicts, enforces capacity, produces priorities, triages, writes |

### Conflict resolution (the example from the brief)

German wants +2h, Fitness detects no movement, Recovery detects worse sleep,
Career has a job with a deadline in 2 days. Orchestrator:

1. Sustainability Index is low → capacity = 60–70 % of normal, cognitive-load cap.
2. Deadline-bound career action gets an urgency boost and is **pinned** (irreversible
   opportunity cost if missed).
3. Recovery/movement actions are low cost and *raise* tomorrow's capacity →
   always admitted when sustainability is below threshold (protected slots).
4. German +2h is **shrunk** to the highest-value 30-45 min block on the weakest skill
   (the agent proposes a "minimum effective dose" variant for every big action).
5. Everything not admitted is listed as **deferred, with a reason**.

---

## 3. Database architecture

SQLite with foreign keys on, all timestamps UTC, dates in the user's timezone.
Free-text columns that may hold sensitive content (`notes`, memory `content`,
health notes) use `EncryptedText`.

Core tables (see `apex/models.py` for exact columns):

| Group | Tables |
|---|---|
| Identity & security | `users`, `auth_sessions`, `audit_log`, `events`, `autonomy_grants`, `job_runs` |
| Direction | `goals`, `skills`, `habits`, `habit_logs`, `plan_items` |
| Learning | `learning_sessions` (common: domain, start, minutes, method, material, breaks, focus, fatigue, distractions) → `language_sessions` (skill, mode active/passive, accuracy) / `law_sessions` (area, activity) ; `tests`, `questions`, `errors` |
| Body & attention | `sleep`, `energy`, `recovery`, `movement`, `workouts`, `screen_time`, `deep_work` |
| Career | `career_opportunities` (source + retrieved_at mandatory for web data), `applications`, `skills_gap` |
| Agent loop | `recommendations`, `inbox_items`, `agent_actions`, `experiments`, `agent_memory`, `research_items` |
| Reviews | `daily_reviews`, `weekly_reviews`, `monthly_reviews` |

Design rules:
- **Raw observations are immutable facts with a `source`** (`manual`, `import:<x>`, `integration:<x>`).
- **Derived values are recomputed**, not stored, unless they are a point-in-time record
  (reviews, recommendations, decisions) — avoids stale denormalized data.
- Dedupe: `inbox_items.dedupe_key`, `agent_actions.idempotency_key`, `job_runs(job, period)` unique.

---

## 4. Memory architecture

Single table `agent_memory`, layered by `layer`:

| Layer | Content | Default lifetime |
|---|---|---|
| `working` | current cycle scratch (today's capacity, admitted plan) | expires end of day |
| `daily` | daily facts & summaries | review after 30 days |
| `long_term` | stable facts about the user (preferences, constraints) | review after 180 days |
| `skill` | skill level evidence | review after 90 days |
| `career` | career facts, targets, applications history | review after 90 days |
| `learning` | Digital Twin findings (HOW I LEARN BEST …) with epistemic label | review after 60 days |
| `decision` | every Orchestrator decision + rationale + later outcome | kept |

Each record: `timestamp, source, confidence (0–1), category, relevance (0–1),
review_at, expires_at, epistemic label, superseded_by`.

Operations: `remember`, `recall(layer, category)`, `correct` (writes a new version and
supersedes the old one — history preserved), `forget` (hard delete, audited without
content), `sweep` (expire working memory, surface records due for review in the Inbox).

**Personal Digital Twin** = the current non-superseded `learning` + `long_term`
records grouped by question (WHEN I WORK BEST, HOW I LEARN BEST, WHAT CAUSES FAILURE,
WHAT IMPROVES RETENTION, WHAT DESTROYS FOCUS, HOW MUCH WORK IS SUSTAINABLE, WHICH
INTERVENTIONS WORK). It is updated by the Learning agent each cycle — only from
observable data.

---

## 5. Autonomy & security model

### Autonomy levels

| Level | Meaning | Examples |
|---|---|---|
| 0 OBSERVE | record only | log a metric |
| 1 RECOMMEND | proposal in Inbox | "do 30 min speaking" |
| 2 PREPARE | draft prepared, awaits approval | CV tailoring plan |
| 3 EXECUTE SAFE | pre-authorized, reversible, internal, low-risk | add a plan item, create a calendar *draft* |
| 4 REQUIRES APPROVAL | external / significant | everything below |

**Hard-forbidden auto-actions** (cannot be granted at level 3, enforced in code
and covered by tests): `send_application`, `publish_linkedin`, `send_message`,
`spend_money`, `medical_decision`, `medication_change`, `legal_commitment`,
`delete_important_data`.

Level 3 requires all of: action type in `SAFE_ACTION_TYPES`, `reversible=True`,
an active `autonomy_grants` row for that action type, under the per-agent daily cap
(runaway guard), and a fresh idempotency key (duplicate guard).

### Security controls

| Control | Implementation |
|---|---|
| Authentication | scrypt (n=2¹⁴) password hash; 32-byte random session tokens stored as SHA-256 hashes; 14-day expiry |
| Authorization | every route requires the owner session; agent least privilege via `AgentContext` scopes |
| CSRF | SameSite=Strict cookie + Origin check on unsafe methods |
| Encryption | Fernet field-level for sensitive text; key from `APEX_DATA_KEY` or a 0600 key file outside the DB |
| Secrets | env / `.env` only, never in DB or logs; integrations read their own named secret |
| Audit | `audit_log` for every mutation and agent action (no sensitive payloads) |
| Data minimization | agents receive only scoped tables; integrations declare needed scopes |
| Approval gates | `autonomy.py` single choke point; UI approval queue |
| Backup / export / delete | `/settings/export` (JSON of all user data), `apex backup` (SQLite online backup), per-record delete, full wipe with typed confirmation |
| Prompt injection | external text stored as quarantined data (`untrusted.py`), never concatenated into instructions, suspicious patterns flagged; agents cannot trigger external actions |

### Failure modes

| Failure | Mitigation |
|---|---|
| hallucinations / bad recommendations | core engines are deterministic and explainable; every recommendation has DATA USED + confidence; LLM output (later) only drafts text, never decides |
| incorrect / outdated web info | `source` + `retrieved_at` mandatory; `review_at` on time-sensitive memory; salary shown only with a source |
| duplicate actions | idempotency keys, dedupe keys, unique job periods |
| runaway agents | agents are pure; per-agent daily action cap; single writer |
| API failures / missing integration | integration registry with status; every agent degrades to "insufficient data" |
| missing data | explicit `insufficient data` states with what is needed — no fake numbers |
| incorrect correlations | Causality Guardrail: n, effect size, confidence; correlations never shown as causes; only experiments yield TESTED_EFFECT |

---

## 6. Integrations

`integrations/registry.py` holds adapters with `status()` →
`not_configured | ok | error` and the scopes/secrets they need. Missing
integrations never block the system. Planned adapters: OpenAI Responses /
Agents API (drafting, summarization, web search with citations), Codex (dev),
MCP (tool bus), Google Calendar (read + drafts), Gmail (read-only digest,
drafts only), job sources (RSS / official APIs), health exports (Google Fit /
Health Connect / CSV), push notifications (Web Push / ntfy).

---

## 7. Phased roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | audit, architecture, repo, schema | done |
| 1 | vertical slice: auth, logging, German, Law, Career, Recovery, Movement, Attention, Productivity, Learning agents, Orchestrator, Priority, Sustainability, Velocity, Memory, Inbox, Morning/Evening/Weekly reviews, dashboard, tests | done |
| 2 | Experiment Engine, Monthly review, Digital Twin page, Radar (manual entry), export/backup | done (RSS import for Radar still open) |
| 3 | LLM layer (OpenAI Responses adapter for drafting + web search with citations), Job Radar sources, LinkedIn advisor | planned |
| 4 | Calendar/Gmail/health integrations, push notifications, Level-3 automations | planned |
| 5 | multi-agent runtime (agents as separate workers via MCP), Postgres, Alembic migrations | planned |

After each phase: **BUILD → TEST → VERIFY → DOCUMENT → COMMIT.**
