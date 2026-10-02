from datetime import date, datetime, timedelta, timezone

import pytest

from apex import autonomy, memory
from apex.models import AgentMemory, CareerOpportunity, PlanItem, Skill

D = date(2026, 10, 2)


@pytest.mark.parametrize("action", sorted(autonomy.FORBIDDEN_AUTO))
def test_forbidden_actions_never_auto_execute(db, action):
    with pytest.raises(autonomy.AutonomyError):
        autonomy.grant(db, action)
    a = autonomy.propose(db, "career", action, {"x": 1}, idempotency_key=f"k:{action}")
    assert a.status == "awaiting_approval" and a.level == 4
    a = autonomy.approve(db, a.id)
    assert a.status == "approved" and "yourself" in a.result["note"]  # no external executor


def test_safe_action_needs_grant_and_is_undoable(db):
    payload = {"day": D.isoformat(), "title": "Walk", "minutes": 30}
    a = autonomy.propose(db, "orchestrator", "add_plan_item", payload, idempotency_key="p1")
    assert a.status == "awaiting_approval" and db.query(PlanItem).count() == 0
    autonomy.grant(db, "add_plan_item")
    b = autonomy.propose(db, "orchestrator", "add_plan_item", payload, idempotency_key="p2")
    assert b.status == "executed" and db.query(PlanItem).count() == 1
    autonomy.undo(db, b.id)
    assert db.query(PlanItem).count() == 0


def test_idempotency_and_runaway_cap(db, monkeypatch):
    a1 = autonomy.propose(db, "x", "something", {}, idempotency_key="same")
    a2 = autonomy.propose(db, "x", "something", {}, idempotency_key="same")
    assert a1.id == a2.id
    from apex import config
    monkeypatch.setattr(config.get_settings(), "agent_daily_action_cap", 3)
    statuses = [autonomy.propose(db, "y", "z", {}, idempotency_key=f"c{i}").status for i in range(5)]
    assert statuses.count("blocked") == 2


def test_prepare_cv_tailoring_is_draft_only(db):
    db.add(Skill(name="German", level=4, evidence="C1"))
    o = CareerOpportunity(title="Counsel", organization="Org", requirements=[
        {"skill": "German", "level": 4}, {"skill": "GDPR", "level": 3}])
    db.add(o)
    db.flush()
    a = autonomy.propose(db, "career", "prepare_cv_tailoring", {"opportunity_id": o.id}, idempotency_key="cv")
    assert a.status == "prepared" and a.level == 2
    assert a.result["emphasize"][0]["skill"] == "German"
    assert a.result["address_gaps"][0]["skill"] == "GDPR"
    assert "never submits" in a.result["note"]


def test_memory_supersede_correct_forget(db):
    m1 = memory.remember(db, "long_term", "pref", "likes mornings", "user", key="k", today=D)
    same = memory.remember(db, "long_term", "pref", "likes mornings", "user", key="k", today=D)
    assert same.id == m1.id
    m2 = memory.remember(db, "long_term", "pref", "likes evenings", "agent", key="k", today=D)
    assert db.get(AgentMemory, m1.id).superseded_by == m2.id
    assert [r.content for r in memory.recall(db, "long_term")] == ["likes evenings"]
    m3 = memory.correct(db, m2.id, "actually: mornings")
    assert m3.confidence == 1.0 and m3.source.startswith("correction")
    memory.forget(db, m3.id)
    assert db.query(AgentMemory).count() == 0  # whole history gone


def test_memory_records_have_metadata_and_expiry(db):
    m = memory.remember(db, "skill", "german", "B2", "test", today=D)
    assert m.review_at == D + timedelta(days=90) and m.source and m.confidence is not None
    w = memory.remember(db, "working", "tmp", "x", "o", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert w not in memory.recall(db, "working")
    assert memory.sweep(db, D)["expired"] == 1


def test_encrypted_at_rest(db):
    memory.remember(db, "long_term", "secret", "very private note", "user", today=D)
    db.commit()
    raw = db.connection().exec_driver_sql("select content from agent_memory").scalar()
    assert "private" not in raw
    assert memory.recall(db, "long_term")[0].content == "very private note"


