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
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    llm_daily_call_cap: int = 40  # cost / runaway guard

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
