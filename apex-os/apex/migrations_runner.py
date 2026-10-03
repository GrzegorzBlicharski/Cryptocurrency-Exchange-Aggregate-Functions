"""Programmatic Alembic: no alembic.ini needed."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

HEAD = "head"


def config(url: str | None = None, connection=None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def upgrade(revision: str = HEAD, url: str | None = None) -> None:
    command.upgrade(config(url), revision)


def ensure_schema(engine: Engine) -> str:
    """Bring any database to the current schema.

    - empty DB          → create_all + stamp head
    - v0.1 DB (no alembic_version but tables exist) → stamp baseline, then upgrade
    - versioned DB      → upgrade head
    """
    from .models import Base

    names = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        cfg = config(connection=conn)
        if not names - {"alembic_version"}:
            Base.metadata.create_all(conn)
            command.stamp(cfg, HEAD)
            return "created"
        if "alembic_version" not in names:
            command.stamp(cfg, "0001")
        command.upgrade(cfg, HEAD)
    return "upgraded"
