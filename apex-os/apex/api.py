"""JSON API (/api/v1) — same services as the UI, for mobile clients and future agents."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import autonomy, logbook, memory, reviews, security
from .agents.orchestrator import CyclePlan, Orchestrator
from .audit import audit
from .dataops import export_all
from .db import get_db
from .models import InboxItem
from .web import today

router = APIRouter(prefix="/api/v1")
Auth = Depends(security.current_user)


class LoginIn(BaseModel):
    username: str
    password: str


def plan_to_dict(plan: CyclePlan) -> dict:
    def act(a):
        c = a.action
        return {"key": c.key, "title": c.title, "domain": c.domain, "minutes": c.minutes, "score": a.score,
                "criteria": a.criteria, "note": getattr(a, "note", ""), "reason": getattr(a, "reason", ""),
                "why": c.why.to_dict()}

    return {
        "day": plan.day.isoformat(),
        "apex_score": plan.apex_score,
        "apex_explain": plan.apex_explain,
        "bottleneck": plan.bottleneck,
        "sustainability": asdict(plan.sustainability),
        "capacity_min": plan.capacity_min,
        "capacity_left_min": plan.capacity_left_min,
        "next_best_actions": [act(a) for a in plan.admitted],
        "deferred": [act(d) for d in plan.deferred],
        "domains": {k: asdict(r.status) for k, r in plan.reports.items()},
        "signals": [{"kind": s.kind, "severity": s.severity, "title": s.title, "so_what": s.so_what,
                     "insight": s.insight.to_dict()} for s in plan.signals],
    }


@router.post("/auth/login")
def api_login(body: LoginIn, db: Session = Depends(get_db)):
    token = security.login(db, body.username, body.password)
    if not token:
        raise HTTPException(401, "invalid credentials")
    return {"token": token, "type": "bearer"}


@router.get("/status")
def status(user=Auth, db: Session = Depends(get_db)):
    return plan_to_dict(Orchestrator().run_cycle(db, today()))


@router.get("/brief")
def brief(user=Auth, db: Session = Depends(get_db)):
    return reviews.morning_brief(db, today())


@router.get("/review/weekly")
def weekly(user=Auth, db: Session = Depends(get_db)):
    return reviews.weekly_review(db, today())


@router.get("/review/monthly")
def monthly(user=Auth, db: Session = Depends(get_db)):
    return reviews.monthly_review(db, today())


@router.get("/log/kinds")
def log_kinds(user=Auth):
    return {k: {"label": v["label"], "fields": [f.__dict__ for f in v["fields"]]} for k, v in logbook.SPECS.items()}


@router.post("/log/{kind}", status_code=201)
def log(kind: str, body: dict, user=Auth, db: Session = Depends(get_db)):
    try:
        obj = logbook.create(db, kind, body, source=str(body.get("_source", "manual"))[:64])
        db.commit()
    except logbook.ValidationError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    return {"id": obj.id, "kind": kind}


@router.get("/inbox")
def inbox(user=Auth, db: Session = Depends(get_db)):
    items = (db.query(InboxItem).filter(InboxItem.status.in_(("unread", "read")))
             .order_by(InboxItem.priority.desc()).limit(100).all())
    return [{"id": i.id, "kind": i.kind, "title": i.title, "so_what": i.so_what, "priority": i.priority,
             "interrupt": i.interrupt, "status": i.status, "why": i.why,
             "deadline": i.deadline.isoformat() if i.deadline else None} for i in items]


@router.post("/actions/{aid}/{op}")
def action(aid: int, op: str, user=Auth, db: Session = Depends(get_db)):
    fn = {"approve": autonomy.approve, "reject": autonomy.reject, "undo": autonomy.undo}.get(op)
    if not fn:
        raise HTTPException(400)
    try:
        a = fn(db, aid)
    except (autonomy.AutonomyError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return {"id": a.id, "status": a.status, "result": a.result}


@router.get("/memory")
def memory_list(layer: str | None = None, user=Auth, db: Session = Depends(get_db)):
    return [{"id": m.id, "layer": m.layer, "category": m.category, "content": m.content, "source": m.source,
             "confidence": m.confidence, "epistemic": m.epistemic, "created_at": m.created_at.isoformat(),
             "review_at": m.review_at.isoformat() if m.review_at else None}
            for m in memory.recall(db, layer=layer)]


@router.delete("/memory/{mid}", status_code=204)
def memory_delete(mid: int, user=Auth, db: Session = Depends(get_db)):
    try:
        memory.forget(db, mid)
    except KeyError:
        raise HTTPException(404) from None
    db.commit()


@router.get("/export")
def export(user=Auth, db: Session = Depends(get_db)):
    audit(db, "user", "data.export", via="api")
    db.commit()
    return export_all(db)
