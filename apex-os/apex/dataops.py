"""Export, backup and delete — the user owns the data."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from .audit import audit
from .models import Base

EXCLUDE_EXPORT = {"auth_sessions", "users"}
KEEP_ON_WIPE = {"users", "auth_sessions", "audit_log"}


def export_all(db: Session) -> dict:
    """Every user-data table as JSON (encrypted columns are decrypted: it's your export)."""
    out: dict = {"exported_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        name = cls.__tablename__
        if name in EXCLUDE_EXPORT:
            continue
        cols = [c.key for c in mapper.column_attrs]
        out["tables"][name] = [{c: getattr(row, c) for c in cols} for row in db.query(cls).all()]
    return out


def wipe_all(db: Session) -> int:
    """Delete all personal data rows. Keeps the login and the audit trail of the wipe itself."""
    n = 0
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in KEEP_ON_WIPE:
            continue
        n += db.execute(table.delete()).rowcount or 0
    audit(db, "user", "data.wipe", rows=n)
    db.commit()
    return n


def backup_sqlite(db_path: Path, dest_dir: Path) -> Path:
    """Consistent online backup via the SQLite backup API (encrypted fields stay encrypted)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"apex-backup-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest

