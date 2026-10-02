"""Audit log and a tiny in-process event bus."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from sqlalchemy.orm import Session

from .models import AuditLog, Event

_subscribers: dict[str, list[Callable[[Session, Event], None]]] = defaultdict(list)


def audit(db: Session, actor: str, action: str, target: str = "", **detail) -> None:
    """Record an auditable action. Pass identifiers only, never sensitive content."""
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))


def subscribe(kind: str, handler: Callable[[Session, Event], None]) -> None:
    _subscribers[kind].append(handler)


def publish(db: Session, event_kind: str, /, **payload) -> Event:
    ev = Event(kind=event_kind, payload=payload)
    db.add(ev)
    db.flush()
    for handler in _subscribers.get(event_kind, []) + _subscribers.get("*", []):
        handler(db, ev)
    return ev


def last_event_id(db: Session, kinds: tuple[str, ...] | None = None) -> int:
    q = db.query(Event.id)
    if kinds:
        q = q.filter(Event.kind.in_(kinds))
    row = q.order_by(Event.id.desc()).first()
    return row[0] if row else 0
