# APEX OS — Personal Operating Agent

> **v0.3:** autonomous Claude agents. Set a goal, and APEX starts an agent that plans its own work,
> researches the web, records leads and findings, puts concrete tasks into your plan, runs experiments
> and schedules its own next run. A Chief of Staff agent coordinates the others. You supervise on
> **/missions**: answer questions, approve the actions that stay yours (applying, messaging,
> publishing, paying, legal and medical decisions), and pause anything with one click.

A personal AI Chief of Staff. It is a closed loop: **observe → measure → analyze → detect →
prioritize → recommend → act when authorized → measure → learn → adapt.** It is not a
habit tracker with AI features added on.

> Maximize verified long-term progress, healthy functioning, capability and life quality
> **per sustainable hour of effort.** Hours, streaks and task counts are not the goal.

Prototype (v0.3) architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Target system: **[APEX OS Technical Specification v1.0](docs/spec/00-index.md)**, which includes the Agent Factory and is pending approval.

## Quick start

```bash
cd apex-os
pip install -e ".[dev]"          # Python 3.11+
cp .env.example .env              # optional; defaults work
python -m apex demo               # optional: synthetic demo data (source='demo')
python -m apex serve              # http://127.0.0.1:8000 → first visit creates the owner account
python -m pytest                  # 78 tests
```

The app runs in the browser, so it works on a Chromebook and a phone. To reach it from your
phone, run it on a home server or VPS behind HTTPS. Set `APEX_COOKIE_SECURE=true` when you do.

Other commands: `python -m apex cycle` (plan now), `brief`, `tick` (run due scheduled jobs),
`backup`, `demo --remove`, `create-user <name>`.

## What works (v0.2)

| Area | Status |
|---|---|
| Dashboard: APEX score, #1 priority, next best actions, domains, sustainability, velocity, experiments, alerts, deadlines; WHY? on everything | ✅ |
| Orchestrator: scoped agents, isolation of failing agents, capacity from sustainability, execution realism and meetings, deadline pinning, protected recovery, minimum-dose shrinking | ✅ |
| 11 agents: German, Law (knowledge graph), Career, LinkedIn, Recovery, Movement, Attention, Execution, Learning, Research/Radar, Comms | ✅ |
| Priority Engine, Sustainability Index, Progress Velocity, causality labels, Experiment Engine | ✅ |
| Logging (13 kinds), JSON API, CSV import, Apple Health import | ✅ |
| Briefs and evening, weekly and monthly reviews; idempotent scheduler with integration syncs | ✅ |
| Inbox with triage and a notification budget; ntfy or webhook push for interrupts only | ✅ |
| Autonomy levels 0–4, approvals, undo, runaway cap, idempotency, forbidden actions | ✅ |
| Layered memory, digital twin, correct, delete and review-due | ✅ |
| **Autonomous missions** (Claude `claude-opus-5-5`, web search and fetch, self-planning, Chief of Staff, budgets, kill switch, supervision UI) | ✅ |
| Claude drafts and extraction (CV, LinkedIn, job requirements). Optional; everything else works without a key | ✅ |
| Job sources (RSS/Atom, Arbeitnow), Radar feeds, calendar ICS in and plan feed out, IMAP read-only | ✅ |
| MCP server (`python -m apex mcp`), Alembic migrations, PWA (installable on a phone), Docker | ✅ |
| Security: scrypt auth, strict cookies, origin check, CSP, field encryption, audit, export, wipe, backup, prompt-injection quarantine, SSRF guard | ✅ |

## Configuration

Copy `.env.example` to `.env`. Every integration is optional: leave it empty and APEX keeps
working and reports it as "not configured". Non-secret settings (feed URLs, keywords) are edited in
**Settings**. Secrets are read only from the environment.

## Use from your phone

Deploy with `docker compose up -d` behind HTTPS (Caddy, Traefik or Cloudflare Tunnel), open the app
and choose "Add to home screen". The service worker caches static files only, never your data.

## MCP

```json
{"mcpServers": {"apex": {"command": "python", "args": ["-m", "apex", "mcp"], "cwd": "/path/to/apex-os"}}}
```
Tools: `apex_status`, `apex_brief`, `apex_inbox`, `apex_law_knowledge_graph`, `apex_digital_twin`,
`apex_log`. AI clients can read status and log observations, but cannot approve actions or delete data.

## Repository layout

```
apex/            application package (see docs/ARCHITECTURE.md §1 module map)
apex/agents/     one module per subagent + orchestrator.py
apex/engines/    priority, sustainability, velocity, insights, notifications, experiments, radar
apex/web/        Jinja templates + static CSS/JS (no build step)
tests/           pytest suite
```
