# 05 — Security, Backup/Export, Threat Model, Failure Modes

## 6. Security architecture (ADR-21)

| Control | Design |
|---|---|
| Network | No public port. Access only over Tailscale (device-authenticated WireGuard), with HTTPS from Tailscale certs. The web binds to 127.0.0.1 behind `tailscale serve`. |
| Authentication | Single principal. **Passkeys (WebAuthn)** primary, plus a password and TOTP fallback for recovery. Sessions: HttpOnly, Secure, SameSite=Strict cookies, rotating, 14 d idle expiry, device list with revoke. |
| Machine identities | Device tokens for automations (iOS Shortcut, companion apps), scoped to `events:write:<types>` only. MCP via local stdio, or a token scoped to read and log. |
| Authorization | Every API call → principal session or scoped token → capability check. Agent runs use capability tokens (doc 02 §8). |
| Encryption in transit | TLS (Tailscale); outbound HTTPS only (SSRF guard: no private/loopback/link-local targets, per-hop redirect checks). |
| Encryption at rest | Full-disk encryption on the host (required) plus **field-level** XChaCha20-Poly1305 (libsodium) for `sensitivity ∈ {personal, health, finance, identity}` columns and blobs. Keys: a data key wrapped by a master key from the OS keychain or a passphrase-derived key (Argon2id); on a VPS the master key can be kept off-host and unlocked at boot. |
| Secrets | Secrets store (encrypted table plus master key). Only adapters can resolve them. Never in logs, prompts or exports (redacted). |
| Data minimization | Agents get only their context sources. LLM calls carry the minimum fields. Per-provider data-class policy (e.g. health → local model or not sent). Email bodies are stored only for allow-listed career senders, otherwise headers only. |
| Audit | Append-only, hash-chained `audit_log` (every command, policy decision, approval, agent lifecycle event, export, login). Verifiable with `apex audit verify`. |
| Approval gates | Constitution (doc 02), enforced in `PolicyService`, which is the only path to side effects. |
| Supply chain | Lockfile, `pnpm audit` in CI, Renovate with a review gate, pinned container digests, SBOM per release. |
| Browser security | Strict CSP (no inline script), Trusted Types, frame-ancestors none, CSRF via SameSite plus Origin check on mutations. |

## 22. Backup & export strategy

| Item | Design |
|---|---|
| Backups | Nightly consistent SQLite snapshot (online backup API) plus blobs → tar → **age**-encrypted to the principal's public key → local disk (7 daily, 4 weekly, 12 monthly) plus an off-site target (S3-compatible or another device over Tailscale). Before every migration: an automatic snapshot. |
| Restore drills | A weekly automated job restores the latest backup into a scratch DB, runs integrity checks (`PRAGMA integrity_check`, row counts, audit chain verify) and reports. A failed drill → CRITICAL inbox item. |
| Export | `GET /api/v1/export` (and CLI): JSON (all tables, decrypted for the principal, with schema versions), CSV per table, the raw `events` NDJSON, the SQLite file, and documents. Signed manifest with hashes. No vendor lock-in: the formats are documented in `docs/export-format.md`. |
| Delete | Per-record forget (memory, events via retraction plus purge job), per-domain purge, full wipe (typed confirmation plus passkey re-auth). Backups expire per retention; the principal can trigger a backup purge. |
| Portability | Postgres migration path (ADR-05); export/import round-trip tested in CI. |

## 23. Threat model

**Assets:**
- the personal dataset (raw events, health, career, decisions),
- credentials and API keys,
- the authority to act (approvals, integrations),
- the agent organization (manifests, policies),
- availability of the system,
- the principal's reputation.

| Threat (STRIDE) | Vector | Mitigations | Residual |
|---|---|---|---|
| **Prompt injection** (Tampering/EoP) | web pages, job posts, emails, documents instruct agents | External content always wrapped and labeled as data; tools gated by policy, not by prompt; agents cannot widen permissions; constitution actions always need approval; adversarial suite in sandbox; provenance checks; Critic on risk ≥ medium | An injected page can still bias an *analysis*, so results carry sources and the Critic or Verification agents check claims |
| Data exfiltration via agent | agent sends data to an attacker URL (fetch with query params, generated tool egress) | Web fetch only of URLs found in search results or pre-approved domains; no agent-controlled POST to arbitrary hosts; generated tools have an egress allow-list; data classes blocked per provider | Low |
| Malicious or buggy generated tool | Tool Factory code | Deno sandbox plus container, no secrets, static bans, tests, security review, human approval for any side effects | Low |
| Privilege escalation via agent creation | Factory or parent creates a powerful child | Intersection rule at compile and call time; immutable security fields; population limits; property tests (P1–P4) | Very low |
| Stolen device or session | phone/laptop theft | Passkeys, device revoke, short sessions, Tailscale device removal, encrypted data at rest | Low |
| Compromised API key | leaked LLM/integration key | Keys only in the secrets store and adapters; never in prompts or logs; per-provider spend limits; rotation runbook | Medium (provider-side) |
| Provider data exposure | LLM provider retains data | Minimization, data-class routing (sensitive → local or none), zero-retention options where available, no raw health or identity documents to external models by default | Medium |
| Cost runaway | agent loops, team explosion | Budgets per run, agent, team, day and month with hard stops; step limits; circuit breakers; population caps; anomaly alert on spend velocity | Low |
| Data poisoning of memory or KG | wrong inferences become "facts" | Confidence and provenance on every record; contradiction detection; principal confirmation for inferred preferences; agent-written facts never reach confidence 1.0 | Medium → monitored |
| Fake opportunities / scams | job postings phishing | Opportunity Scout checks domain reputation and employer site; flags requests for money or IDs; never auto-applies | Low |
| Repudiation | "who did this?" | Hash-chained audit, `correlation_id` from trigger to action | Very low |
| Denial of service | runaway jobs fill disk/CPU | Queue concurrency limits, disk quota alerts, log rotation, DLQ | Low |
| Insider (the principal) error | accidental wipe or grant | Typed confirmations, passkey re-auth for destructive ops, backups, grant expiries | Low |

## 24 / 50. Failure-mode analysis & resilience

| Failure | Detection | Handling |
|---|---|---|
| Model API failure / 429 / 5xx | SDK error | SDK retries with backoff → router fallback chain → checkpoint and reschedule; never a partial silent result |
| Timeout | per-step deadline | abort step → checkpoint → retry once at a higher tier or later |
| Malformed model output | schema validation fails (tool input, structured output) | return an `is_error` tool result with the validation message (the model self-corrects); 2 strikes → fail the step |
| Hallucinated tool call | unknown tool or capability | denied with an error result; counted in fitness (tool_efficiency, hallucination) |
| Refusal | stop reason refusal (after server fallback) | terminal for the step; recorded; no evasion attempts |
| Partial execution | run interrupted mid-plan | checkpoints after each step (messages, tool results, actions); resume job replays from the last checkpoint; external actions have idempotency keys so they never double-execute |
| Duplicated event | same `idempotency_key` | unique constraint → ignored; handler ledger prevents re-processing |
| Interrupted workflow (worker crash) | lease expiry | job is re-claimed; resume from checkpoint |
| Stale data | `as_of` older than freshness SLO per source | metric shows `STALE` with age; agents see the freshness flag; integration health alert |
| Conflicting agent recommendations | Orchestrator merges candidates | conflict resolver: sustainability floor → constitution → deadlines → goal weights → EV/cost; losing recommendations are recorded with the reason; persistent conflicts go to the CoS as a decision item |
| Policy engine bug | property tests and canary checks | fail-closed: any exception in policy evaluation → REQUIRES_APPROVAL |
| Bad migration | health check after migrate | automatic rollback to the pre-migration snapshot and the previous image |
| Analytics regression | golden tests; metric drift monitor (value jumps after a version bump) | versioned metrics: old version kept; alert |
| Disk full | quota monitor | pause non-critical jobs; alert; deletes still allowed |
| Clock skew | NTP check | warn; ordering by ULID within the source |

**Structured logs:** JSON with `ts, level, service, correlation_id, run_id, agent_id, event_id, msg`.
PII is redacted by default. Retention is 30 d, then summary.

**Rollback where possible:** actions with registered `undo` (plan writes, memory writes, calendar
own-events) expose "Undo" for 7 days. Irreversible actions are constitution-gated by definition.

## 51. Observability of the AI itself

The **AI Observability** dashboard (Agent Control Center → Metrics) shows, per agent, model, tool and
period, with n and intervals:

| Metric | Definition |
|---|---|
| agent success rate / verified success rate | doc 02 §3.10 |
| human correction rate | edits, reverts and rejections of agent outputs |
| escalation rate & escalation quality | escalations / runs; rated "needed" / escalations |
| false alert rate | dismissed-as-unnecessary notifications / notifications |
| autonomous completion rate | tasks completed and verified without principal involvement / tasks |
| cost per successful task | € / verified successes |
| time saved | Σ estimated manual minutes of verified autonomous tasks (task-type baselines you set or measure) |
| model performance | verified success × cost × latency per model and task type |
| tool failure rate | failed / invocations, per tool |
| forecast calibration | Brier score / interval coverage of forecasts |

**Intelligence improvement loop (§52):**

```
ACTION → RESULT → VERIFICATION → FEEDBACK (corrections, ratings) → MEMORY (procedural/failure/success)
→ POLICY UPDATE (proposal) → better future action
```

Policy updates proposed by the loop may tune *thresholds within constitutional bounds*, routing
priors and verification intensity. They are applied only after review: automatically for
routing priors, and with principal approval for thresholds. Security policies and the constitution
are out of reach.
