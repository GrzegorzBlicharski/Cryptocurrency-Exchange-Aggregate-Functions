# APEX OS — Personal Operating Agent

A personal AI Chief of Staff. It is a closed loop: **observe → measure → analyze → detect →
prioritize → recommend → act when authorized → measure → learn → adapt.** It is not a
habit tracker with AI features added on.

> Maximize verified long-term progress, healthy functioning, capability and life quality
> **per sustainable hour of effort.** Hours, streaks and task counts are not the goal.

Architecture, agents, schema, memory, autonomy/security model and roadmap are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

```bash
cd apex-os
pip install -e ".[dev]"          # Python 3.11+
cp .env.example .env              # optional; defaults work
python -m apex demo               # optional: synthetic demo data (source='demo')
python -m apex serve              # http://127.0.0.1:8000 → first visit creates the owner account
python -m pytest                  # 37 tests
```

The app runs in the browser, so it works on a Chromebook and a phone. To reach it from your
phone, run it on a home server or VPS behind HTTPS. Set `APEX_COOKIE_SECURE=true` when you do.

Other commands: `python -m apex cycle` (plan now), `brief`, `tick` (run due scheduled jobs),
`backup`, `demo --remove`, `create-user <name>`.

## What works today (phase 1 + most of phase 2)

| Area | Status |
|---|---|
| Dashboard (APEX score, #1 priority, next best actions, domains, sustainability, velocity, experiments, alerts, deadlines, WHY? on everything) | ✅ |
| Orchestrator: scoped agents, conflict resolution under capacity, deadline pinning, protected recovery, shrinking big actions to a minimum dose, deferral reasons | ✅ |
| Agents: German, Law (knowledge graph), Career (high-signal filter + gap analysis), Recovery (detect → flag → explain → next step, no diagnosis), Movement, Attention, Execution, Learning | ✅ |
| Priority Engine, Sustainability Index, Progress Velocity, Causality Guardrail labels | ✅ |
| Daily logging (13 kinds) + JSON API + CSV import | ✅ |
| Morning brief, evening review, weekly and monthly reviews; idempotent scheduler | ✅ |
| Inbox with triage and a notification budget (anti-nagging) | ✅ |
| Autonomy levels 0–4, approval queue, undo, runaway cap, idempotency, forbidden actions | ✅ |
| Layered memory, digital twin, correct/delete/review-due | ✅ |
| Experiment engine (randomized day assignment, TESTED_EFFECT, continue/modify/reject) | ✅ |
| Radar with an expected-value gate (manual entry) | ✅ |
| Security: scrypt auth, HttpOnly SameSite=Strict sessions, origin check, CSP, field encryption, audit log, export, wipe, backup, prompt-injection quarantine | ✅ |
| LLM drafting, web search, job sources, LinkedIn advisor, Calendar/Gmail/health integrations, push | ⏳ planned. Listed honestly as "planned" in Settings, not simulated |

## Repository layout

```
apex/            application package (see docs/ARCHITECTURE.md §1 module map)
apex/agents/     one module per subagent + orchestrator.py
apex/engines/    priority, sustainability, velocity, insights, notifications, experiments, radar
apex/web/        Jinja templates + static CSS/JS (no build step)
tests/           pytest suite
```
