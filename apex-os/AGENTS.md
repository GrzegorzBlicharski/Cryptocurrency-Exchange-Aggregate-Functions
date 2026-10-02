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
