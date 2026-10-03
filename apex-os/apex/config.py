"""Runtime configuration. All values come from environment / .env (prefix APEX_)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APEX_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/apex.db"
    data_dir: Path = Path("./data")
    data_key: str = ""
    timezone: str = "Europe/Warsaw"

    # Anti-nagging: max interrupting notifications per day; everything else is digested.
    interrupt_budget_per_day: int = 3
    # Runaway guard: max actions a single agent may create per day.
    agent_daily_action_cap: int = 20

    scheduler_enabled: bool = True
    morning_brief_hour: int = 6
    evening_review_hour: int = 21
    weekly_review_weekday: int = 6  # Sunday

    # Personal defaults used until goals/targets say otherwise.
    sleep_target_hours: float = 7.5
    steps_target: int = 8000
    daily_focus_capacity_min: int = 300  # sustainable focused minutes on a normal day

    session_days: int = 14
    cookie_secure: bool = False  # set true behind HTTPS

    # ---- integrations (secrets come from env only; absent = not configured) ----
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "high"  # autonomous missions; extraction uses "low"
    llm_daily_call_cap: int = 60  # single-shot drafting/extraction calls per day

    # ---- autonomous missions ----
    missions_enabled: bool = True  # global kill switch (also toggleable in Settings)
    mission_max_steps: int = 16  # model turns per mission run
    mission_max_web_searches: int = 8  # per model turn (server tool max_uses)
    mission_daily_token_cap: int = 2_000_000  # all missions, input+output tokens per day
    mission_runs_per_tick: int = 2
    max_active_missions: int = 12

    ics_url: str = ""  # private "secret address in iCal format" of your calendar
    calendar_feed_token: str = ""  # enables /calendar/<token>.ics (read-only plan feed)

    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""  # use an app password, never your main password
    imap_folder: str = "INBOX"

    ntfy_url: str = ""  # e.g. https://ntfy.sh/<long-random-topic>
    ntfy_token: str = ""
    webhook_url: str = ""  # optional generic push target (POST JSON)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
