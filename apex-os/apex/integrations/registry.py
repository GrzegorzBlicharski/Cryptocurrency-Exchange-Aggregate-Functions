"""Integration registry with honest, live status. Missing integrations never block the core loop."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import Settings
from . import store


@dataclass
class Integration:
    key: str
    name: str
    purpose: str
    scopes: tuple[str, ...]
    needs: str  # what configures it
    store_key: str | None = None
    planned: bool = False

    def status(self, settings: Settings, db: Session | None) -> str:
        if self.planned:
            return "not built (see docs)"
        ok = {
            "csv_import": True, "apple_health": True, "mcp": True,
            "claude": bool(settings.anthropic_api_key),
            "claude_web": bool(settings.anthropic_api_key),
            "missions": bool(settings.anthropic_api_key) and settings.missions_enabled,
            "google_calendar": bool(settings.ics_url),
            "calendar_feed": len(settings.calendar_feed_token) >= 24,
            "gmail": bool(settings.imap_host and settings.imap_user and settings.imap_password),
            "notifications": bool(settings.ntfy_url or settings.webhook_url),
        }.get(self.key)
        if self.key == "job_sources" and db is not None:
            cfg = store.get(db, "job_sources")
            ok = bool(cfg.get("feeds") or cfg.get("arbeitnow"))
        if self.key == "radar_feeds" and db is not None:
            cfg = store.get(db, "radar")
            ok = bool(cfg.get("feeds") or (cfg.get("web_queries") and settings.anthropic_api_key))
        if not ok:
            return "not configured"
        if self.store_key and db is not None:
            when, st = store.status(db, self.store_key)
            if when:
                return f"ok · last sync {when:%Y-%m-%d %H:%M} · {st}"
        return "ok"


INTEGRATIONS = [
    Integration("csv_import", "CSV import", "Import any app's export (sleep, steps, screen time…)",
                ("sleep", "movement", "screen_time", "energy", "workouts", "deep_work"), "built-in"),
    Integration("apple_health", "Apple Health import", "export.xml → steps, sleep, resting HR, HRV",
                ("movement", "sleep", "recovery"), "built-in (upload below)"),
    Integration("claude", "Claude (Anthropic API)", "Drafts (CV, LinkedIn), requirement extraction",
                ("career_opportunities", "skills", "linkedin_profile"), "env ANTHROPIC_API_KEY"),
    Integration("claude_web", "Claude web search + fetch", "Agents browse the web; only URLs actually read are saved",
                ("research_items", "career_opportunities"), "env ANTHROPIC_API_KEY"),
    Integration("missions", "Autonomous missions", "Goal-driven Claude agents that plan, browse and act within gates",
                ("all (via scoped tools)",), "env ANTHROPIC_API_KEY + Missions page"),
    Integration("job_sources", "Job sources", "RSS/Atom feeds + Arbeitnow API → Career agent",
                ("career_opportunities",), "settings below", "job_sources"),
    Integration("radar_feeds", "Radar feeds", "RSS/Atom + web queries → Radar (EV-gated)",
                ("research_items",), "settings below", "radar"),
    Integration("google_calendar", "Calendar (ICS read)", "Busy time reduces focus capacity",
                ("calendar_events",), "env APEX_ICS_URL", "calendar"),
    Integration("calendar_feed", "Plan → calendar feed", "Subscribe to /calendar/<token>.ics (read-only)",
                ("plan_items",), "env APEX_CALENDAR_FEED_TOKEN (≥24 chars)"),
    Integration("gmail", "Gmail / IMAP (read-only)", "Interview/offer/deadline emails, headers only",
                ("mail_items",), "env APEX_IMAP_HOST / _USER / _PASSWORD (app password)", "mail"),
    Integration("notifications", "Push (ntfy / webhook)", "Budgeted interrupts only",
                ("inbox_items",), "env APEX_NTFY_URL or APEX_WEBHOOK_URL", "notifications"),
    Integration("mcp", "MCP server", "Expose APEX read tools to AI clients: python -m apex mcp", (), "built-in"),
    Integration("google_oauth", "Google Calendar/Gmail write via OAuth", "Intentionally not built: APEX never sends "
                "mail or edits your calendar; ICS + IMAP read-only cover the use-cases", (), "—", planned=True),
    Integration("computer_use", "Browser / computer use", "Intentionally not built: external actions stay manual "
                "behind approval gates", (), "—", planned=True),
]


def statuses(settings: Settings, db: Session | None = None) -> list[dict]:
    return [{"key": i.key, "name": i.name, "purpose": i.purpose, "status": i.status(settings, db),
             "scopes": i.scopes, "needs": i.needs} for i in INTEGRATIONS]
