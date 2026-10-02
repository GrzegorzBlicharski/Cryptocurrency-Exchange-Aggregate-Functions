import os
import tempfile
from datetime import date

import pytest

_tmp = tempfile.mkdtemp(prefix="apex-test-")
os.environ.update({"APEX_DATABASE_URL": "sqlite://", "APEX_DATA_DIR": _tmp, "APEX_SCHEDULER_ENABLED": "false"})

from apex import db as apex_db  # noqa: E402
from apex.models import Base  # noqa: E402

TODAY = date(2026, 10, 2)


@pytest.fixture
def db():
    engine = apex_db.init()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = apex_db.new_session()
    yield s
    s.close()


@pytest.fixture
def demo_db(db):
    from apex import demo

    demo.seed(db, TODAY)
    return db


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from apex import security
    from apex.main import create_app

    security.create_user(db, "owner", "correct horse battery")
    c = TestClient(create_app(start_scheduler=False))
    r = c.post("/login", data={"username": "owner", "password": "correct horse battery"})
    assert r.status_code == 200
    return c
