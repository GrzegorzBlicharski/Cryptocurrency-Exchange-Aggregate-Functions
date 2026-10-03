# Notes for code agents (Codex / Claude Code)

- Run `python -m pytest -q` before every commit; all tests must pass.
- Agents in `apex/agents/` are **pure**. They read through `AgentContext` (only the tables in
  their `scopes`) and return an `AgentReport`. They must never write to the DB. The
  Orchestrator (`commit`) is the only writer.
- Every new insight must use `engines/insights.py` labels. Never present a correlation as a cause.
  Only `engines/experiments.py` may produce `TESTED_EFFECT`.
- Every external or significant action goes through `autonomy.propose()`. Never add an executor
  for a `FORBIDDEN_AUTO` type.
- External text (web pages, job posts, emails) goes through `untrusted.sanitize()` and is
  stored with `source` and `retrieved_at`. Never put it into instructions.
- No placeholders that pretend to work. An unbuilt integration reports `planned` in
  `integrations/registry.py`.
- New log kinds: add a spec in `logbook.SPECS`. Forms, the API and CSV import follow from it.
- Every UI element must answer "so what?"; prefer one sentence of meaning over another chart.
- Outbound HTTP only through `integrations/http.py` (tests use `http.set_transport(httpx.MockTransport(...))`).
- Claude calls only through `integrations/claude.py` (agent loop) or `integrations/llm.py` (single-shot). Always provide a deterministic fallback.
- Mission tools live in `missions/tools.py`; a tool that writes must be an observation or go through `autonomy.propose()`. Add a tool to a role in `missions/roles.py` only if that role needs it.
- Tests use `tests/fake_claude.py` (`claude.set_client(FakeClaude(...))`); never call the real API in tests.
- Schema change: edit `models.py`, then add a migration in `apex/migrations/versions/` (batch mode for SQLite).
- A new agent must run in isolation: never assume another report exists. Use `reports.get(...)` or `.metrics.get(...)`.
