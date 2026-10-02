"""Layered agent memory + Personal Digital Twin.

Layers: working, daily, long_term, skill, career, learning, decision.
Every record: timestamp, source, confidence, category, relevance, review/expiry.
Correction keeps history (supersede); forgetting is a hard delete.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .audit import audit
from .models import AgentMemory

LAYERS = ("working", "daily", "long_term", "skill", "career", "learning", "decision")
REVIEW_DAYS = {"daily": 30, "long_term": 180, "skill": 90, "career": 90, "learning": 60}

TWIN_QUESTIONS = (
    "WHEN I WORK BEST",
    "HOW I LEARN BEST",
    "WHAT CAUSES FAILURE",
    "WHAT IMPROVES RETENTION",
    "WHAT DESTROYS FOCUS",
    "HOW MUCH WORK IS SUSTAINABLE",
    "WHICH INTERVENTIONS ACTUALLY WORK",
)


def remember(
    db: Session,
    layer: str,
    category: str,
    content: str,
    source: str,
    *,
    key: str = "",
    confidence: float = 0.5,
    relevance: float = 0.5,
    epistemic: str = "OBSERVATION",
    review_at: date | None = None,
    expires_at: datetime | None = None,
    today: date | None = None,
) -> AgentMemory:
    """Write a memory. With a `key`, an existing live record with the same layer/category/key
    is superseded only if the content changed (no duplicate rows for unchanged facts)."""
    if layer not in LAYERS:
        raise ValueError(f"unknown memory layer {layer}")
    today = today or date.today()
    if review_at is None and layer in REVIEW_DAYS:
        review_at = today + timedelta(days=REVIEW_DAYS[layer])
    if expires_at is None and layer == "working":
        expires_at = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    prev = None
    if key:
        prev = (db.query(AgentMemory)
                .filter_by(layer=layer, category=category, key=key, superseded_by=None)
                .order_by(AgentMemory.id.desc()).first())
        if prev and prev.content == content and prev.epistemic == epistemic:
            prev.confidence = confidence
            return prev
    rec = AgentMemory(layer=layer, category=category, key=key, content=content, source=source,
                      confidence=confidence, relevance=relevance, epistemic=epistemic,
                      review_at=review_at, expires_at=expires_at)
    db.add(rec)
    db.flush()
    if prev:
        prev.superseded_by = rec.id
    return rec


def recall(db: Session, layer: str | None = None, category: str | None = None,
           include_superseded: bool = False, limit: int = 200) -> list[AgentMemory]:
    q = db.query(AgentMemory)
    if layer:
        q = q.filter_by(layer=layer)
    if category:
        q = q.filter_by(category=category)
    if not include_superseded:
        q = q.filter(AgentMemory.superseded_by.is_(None))
    now = datetime.now(timezone.utc)
    rows = q.order_by(AgentMemory.created_at.desc()).limit(limit).all()
    return [r for r in rows if not r.expires_at or _aware(r.expires_at) > now]


def correct(db: Session, memory_id: int, new_content: str, actor: str = "user") -> AgentMemory:
    """User correction: new version with source=user and full confidence; old one superseded."""
    old = db.get(AgentMemory, memory_id)
    if not old:
        raise KeyError(memory_id)
    new = AgentMemory(layer=old.layer, category=old.category, key=old.key, content=new_content,
                      source=f"correction:{actor}", confidence=1.0, relevance=old.relevance,
                      epistemic="OBSERVATION" if actor == "user" else old.epistemic,
                      review_at=old.review_at, expires_at=old.expires_at)
    db.add(new)
    db.flush()
    old.superseded_by = new.id
    audit(db, actor, "memory.correct", f"memory:{memory_id}", new_id=new.id)
    return new


def forget(db: Session, memory_id: int, actor: str = "user") -> None:
    """Hard delete including its history chain. Audit keeps only the id."""
    rec = db.get(AgentMemory, memory_id)
    if not rec:
        raise KeyError(memory_id)
    chain = [rec]
    while True:
        prev = db.query(AgentMemory).filter_by(superseded_by=chain[-1].id).first()
        if not prev:
            break
        chain.append(prev)
    for r in chain:
        r.superseded_by = None
    db.flush()
    for r in chain:
        db.delete(r)
    audit(db, actor, "memory.forget", f"memory:{memory_id}", versions=len(chain))


def sweep(db: Session, today: date) -> dict:
    """Delete expired working memory; return records due for review."""
    now = datetime.now(timezone.utc)
    expired = [r for r in db.query(AgentMemory).filter(AgentMemory.expires_at.isnot(None)).all()
               if _aware(r.expires_at) <= now]
    for r in expired:
        db.delete(r)
    due = (db.query(AgentMemory)
           .filter(AgentMemory.superseded_by.is_(None), AgentMemory.review_at.isnot(None),
                   AgentMemory.review_at <= today).all())
    return {"expired": len(expired), "due_for_review": due}


def digital_twin(db: Session) -> dict[str, list[AgentMemory]]:
    twin: dict[str, list[AgentMemory]] = {q: [] for q in TWIN_QUESTIONS}
    for r in recall(db, layer="learning"):
        if r.category in twin:
            twin[r.category].append(r)
    for r in recall(db, layer="long_term"):
        if r.category in twin:
            twin[r.category].append(r)
    for v in twin.values():
        v.sort(key=lambda r: -r.confidence)
    return twin


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
