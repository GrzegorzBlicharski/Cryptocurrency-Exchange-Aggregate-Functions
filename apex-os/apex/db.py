"""Engine / session management."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from . import crypto
from .config import Settings, get_settings
from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init(settings: Settings | None = None) -> Engine:
    """Create engine, configure encryption and create tables. Idempotent per URL."""
    global _engine, _SessionLocal
    settings = settings or get_settings()
    crypto.configure(settings.data_key, settings.data_dir)

    url = settings.database_url
    if _engine is not None and str(_engine.url) == url:
        return _engine  # already initialized (keeps in-memory DBs alive across app factories)
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        else:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    Base.metadata.create_all(engine)
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        init()
    assert _engine is not None
    return _engine


def new_session() -> Session:
    if _SessionLocal is None:
        init()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = new_session()
    try:
        yield s
    finally:
        s.close()
