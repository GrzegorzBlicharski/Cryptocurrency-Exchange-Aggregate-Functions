"""Integration registry.

Each integration declares what it needs and reports an honest status. A missing or
failing integration never blocks the core loop. `implemented=False` means the adapter
is designed (see docs/ARCHITECTURE.md §6) but not built yet — the UI says so plainly.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


@dataclass
class Integration:
    key: str
    name: str
    purpose: str
    scopes: tuple[str, ...]
    secret: str | None
    implemented: bool
    phase: int

    def status(self, settings: Settings) -> str:
        if not self.implemented:
            return f"planned (phase {self.phase})"
        if self.secret and not getattr(settings, self.secret, ""):
            return "not configured"
        return "ok"


INTEGRATIONS = [
    Integration("csv_import", "CSV import", "Import sleep/movement/screen-time/energy exports from any app",
                ("sleep", "movement", "screen_time", "energy", "workouts", "deep_work"), None, True, 1),
    Integration("openai_responses", "OpenAI Responses API", "Draft CV/LinkedIn text, summarize, structured extraction",
                ("career_opportunities", "skills"), "openai_api_key", False, 3),
    Integration("openai_web_search", "OpenAI Web Search", "Radar + job search with citations (source + date stored)",
                ("research_items", "career_opportunities"), "openai_api_key", False, 3),
    Integration("openai_agents", "OpenAI Agents API", "Run subagents as hosted agents with tool scopes",
                (), "openai_api_key", False, 5),
    Integration("mcp", "MCP tool bus", "Expose APEX read tools / consume external tools", (), None, False, 5),
    Integration("codex", "Codex", "Development agent for extending APEX itself", (), None, False, 5),
    Integration("google_calendar", "Google Calendar", "Read schedule; create DRAFT focus blocks (approval required)",
                ("plan_items",), None, False, 4),
    Integration("gmail", "Gmail", "Read-only deadline digest; drafts only, never sends", (), None, False, 4),
    Integration("job_sources", "Job sources", "Official job APIs / RSS feeds into the Career agent",
                ("career_opportunities",), None, False, 3),
    Integration("health_connect", "Health data", "Google Fit / Health Connect / wearable exports",
                ("sleep", "movement", "recovery"), None, False, 4),
    Integration("notifications", "Push notifications", "Web Push / ntfy for budgeted interrupts only",
                ("inbox_items",), None, False, 4),
    Integration("computer_use", "Browser / computer use", "Assisted form filling under approval gates",
                (), None, False, 5),
]


def statuses(settings: Settings) -> list[dict]:
    return [{"key": i.key, "name": i.name, "purpose": i.purpose, "status": i.status(settings),
             "scopes": i.scopes, "implemented": i.implemented} for i in INTEGRATIONS]
