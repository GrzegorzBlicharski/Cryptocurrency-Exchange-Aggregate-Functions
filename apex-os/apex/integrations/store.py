"""Non-secret integration settings + sync status (table integration_settings)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import IntegrationSetting

DEFAULTS: dict[str, dict] = {
    "job_sources": {"feeds": [], "arbeitnow": False, "keywords": [], "max_per_sync": 25},
    "radar": {"feeds": [], "web_queries": [], "keywords": []},
    "calendar": {"busy_threshold_min": 60},
    "mail": {"days": 7},
    "notifications": {"enabled": True},
}


def get(db: Session, key: str) -> dict:
    row = db.query(IntegrationSetting).filter_by(key=key).first()
    return {**DEFAULTS.get(key, {}), **(row.value if row else {})}


def put(db: Session, key: str, value: dict) -> None:
    row = db.query(IntegrationSetting).filter_by(key=key).first() or IntegrationSetting(key=key)
    row.value = {**DEFAULTS.get(key, {}), **value}
    db.add(row)


def mark(db: Session, key: str, status: str) -> None:
    row = db.query(IntegrationSetting).filter_by(key=key).first() or IntegrationSetting(key=key, value={})
    row.last_sync_at = datetime.now(timezone.utc)
    row.last_status = status[:300]
    db.add(row)


def status(db: Session, key: str) -> tuple[datetime | None, str]:
    row = db.query(IntegrationSetting).filter_by(key=key).first()
    return (row.last_sync_at, row.last_status) if row else (None, "")
