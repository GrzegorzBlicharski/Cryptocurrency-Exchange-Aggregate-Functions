# 09 — Runtime on a Claude Pro plan (decisions of 2026-10-03)

The principal decided:
- **Hosting:** "here, in the virtual machine", i.e. Claude Code on the web.
- **Model:** stay within the **Claude Pro subscription**, so no paid API.
- **Starting autonomy:** accepted.

This document supersedes the parts of ADR-02, ADR-04 and ADR-16 that assumed an always-on host and API access.

## Facts that shaped the decision

| Fact | Consequence |
|---|---|
| Cloud sessions run in ephemeral containers that are reclaimed after inactivity, and there is no inbound access from the phone | No always-on web server or scheduler in the container |
| The Pro subscription does not include Anthropic API access | No `ANTHROPIC_API_KEY`; the LLM is Claude inside the Claude Code session and, on the page, the `sample` capability billed to the viewer's plan |
| The code repository is **public** | **No personal data in git, ever**, encrypted or not |
| claude.ai Artifacts offer a private, persistent `db`, `sample` (Claude on the viewer's plan), and `mcp` with the built-in *Claude Code Remote* connector | The UI and the system of record can live in an artifact; a button can start agents |
| Routines (scheduled triggers) start fresh Claude Code sessions | They are the scheduler and the agent runtime |

## ADR-23 Runtime = Claude Code sessions started by Routines

- **DECISION.** Agents are Claude Code sessions in this environment:
  - a morning cycle (06:58 Europe/Warsaw),
  - an evening cycle (20:58),
  - an on-demand cycle (the **Run agents** button → `fire_trigger`).

  Each session follows `ops/routines/apex-cycle.md`:
  1. read the artifact db,
  2. run deterministic analytics (`python -m apex bridge`),
  3. do the agent work (research with web tools, mission plans, plan items, approvals),
  4. write back create-only documents,
  5. stop.
- **RATIONALE.** It stays inside Pro; no host to run; web research comes built in.
- **ALTERNATIVES.**
  - A paid API key with the v0.3 mission engine: outside the subscription, rejected by the principal.
  - Desktop app scheduled tasks: needs the principal's computer to be on.
- **TRADE-OFFS.**
  - **Not real-time:** analysis refreshes 2× a day or on demand.
  - Runs consume Pro usage limits. When a limit is reached, a run waits for the reset.
  - Policy enforcement for agent actions is partly **instructional** (the session prompt). It is backed by structural limits: the session has no email, application or payment tools, and the artifact rules allow only owner access.

## ADR-24 System of record = the artifact's private `db`

- **DECISION.**
  - Raw entries are stored as day documents `events/{YYYY-MM-DD}.entries[]`. The 25,000-document cap is reached only after decades.
  - Other collections: `goals`, `skills`, `plan/{day}`, `opportunities`, `projections/{run}`, `inbox`, `approvals`, `missions`, `runs`, `config/runtime`.
  - Access rule: `read: admin, write: admin` on the root, so only the owner can open the data, even if the page is ever shared. Verified: an `interact` read returns nothing.
  - The SQLite DB is **ephemeral**, rebuilt from raw events on every run (ADR-06 raw-events-first pays off).
- **RATIONALE.** Persistent, private, reachable from the phone and from sessions; nothing to host; no personal data in git.
- **ALTERNATIVES.**
  - An encrypted DB in the public repo: rejected, because a leaked or weak key exposes everything forever.
  - A private data repo: possible later for off-platform backups (the principal must create it).
- **TRADE-OFFS.**
  - Data lives in the principal's claude.ai account (Anthropic-hosted).
  - Last-writer-wins on the same day document from two open tabs (rare for one person).
  - Export is a session that dumps the db to JSON for the principal.

## ADR-25 UI = Mission Control artifact

- **DECISION.** A single private artifact page (`ops/artifact/mission-control.html`, published as https://claude.ai/artifact/D22nVFuWAf6WLdYyDo1HvX) with five tabs:
  - **Today:** top 3 with WHY, sustainability, bottleneck, signals, plan, domains.
  - **Log:** natural-language quick capture parsed by `sample` on the principal's plan with a confirm step, plus a manual form.
  - **Decisions:** approvals and inbox.
  - **Agents:** missions, messages to agents, runs with the sources they read.
  - **Goals.**
- **RATIONALE.** It works on phone, Chromebook and desktop; it is private; no server.
- **TRADE-OFFS.** The Next.js design from doc 07 is postponed. The information architecture stays the same; only the rendering technology differs.

## ADR-02 (revised) Stack

- **DECISION.** Keep the **Python core** (v0.3, 81 tests) as the deterministic engine. Sessions call it via the CLI; the TypeScript rebuild is **deferred**.
- **RATIONALE.** With no host, the Next.js server has no place to run; the session environment runs Python out of the box; the tested engine is reused directly.
- **TRADE-OFFS.** Two languages: Python in sessions, JS in the page. Revisit if APEX ever moves to a host.

## ADR-16 (revised) Models

- Agent work uses whatever model the Claude Code session runs, within Pro.
- Quick capture uses `sample` with `modelTier: "quick"`.
- The v0.3 API path (`integrations/claude.py`) stays in the codebase, **disabled** (no key), for a future decision.

## Autonomy (accepted)

| Area | Level |
|---|---|
| default | L2 |
| internal planning (plan items, mission plans, experiments) | L4 |
| research | L3 |
| anything that leaves APEX | L2: prepared as an approval; the principal performs it |

Stored in `config/runtime.autonomy` and restated in the cycle instructions.

## Roadmap impact

| Phase | Status |
|---|---|
| Phase 0–1 (core tracking + Mission Control) | **delivered on this runtime** |
| Phases 2–5 | engine work in Python; extend the bridge projection |
| Phases 6–12 (agents, Factory) | session-based. The Agent Factory manifests (doc 02) become `missions` and agent-manifest documents in the db, created and retired by the Chief of Staff pass under the same reuse-before-create, budget and no-escalation rules |
