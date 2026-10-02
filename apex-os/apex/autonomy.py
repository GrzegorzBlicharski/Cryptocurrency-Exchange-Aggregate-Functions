"""Autonomy levels and approval gates — the single choke point for every agent action.

LEVEL 0 OBSERVE · 1 RECOMMEND · 2 PREPARE · 3 EXECUTE SAFE ACTION · 4 REQUIRES APPROVAL

Hard rule: FORBIDDEN_AUTO action types can never execute without explicit user
approval, cannot be granted at level 3, and in this version have no external
executor at all — after approval the user performs them (APEX does not send,
publish, pay, or commit on the user's behalf).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from enum import IntEnum

from sqlalchemy.orm import Session

from .audit import audit
from .config import get_settings
from .models import AgentAction, AutonomyGrant, CareerOpportunity, PlanItem, Skill


class Level(IntEnum):
    OBSERVE = 0
    RECOMMEND = 1
    PREPARE = 2
    EXECUTE_SAFE = 3
    REQUIRES_APPROVAL = 4


FORBIDDEN_AUTO = frozenset({
    "send_application", "publish_linkedin", "send_message", "spend_money",
    "medical_decision", "medication_change", "legal_commitment", "delete_important_data",
})


class AutonomyError(Exception):
    pass


# --------------------------------------------------------------- internal executors
def _add_plan_item(db: Session, payload: dict) -> dict:
    item = PlanItem(day=date.fromisoformat(payload["day"]), title=payload["title"][:200],
                    domain=payload.get("domain", "other"), planned_min=int(payload.get("minutes", 30)),
                    recommendation_id=payload.get("recommendation_id"))
    db.add(item)
    db.flush()
    return {"plan_item_id": item.id}


def _undo_add_plan_item(db: Session, result: dict) -> None:
    item = db.get(PlanItem, result.get("plan_item_id"))
    if item and item.status == "planned":
        db.delete(item)


def _prepare_cv_tailoring(db: Session, payload: dict) -> dict:
    """Level-2 PREPARE: a tailoring plan for the user. Nothing leaves the system."""
    opp = db.get(CareerOpportunity, payload["opportunity_id"])
    if not opp:
        raise AutonomyError("opportunity not found")
    skills = {s.name.lower(): s for s in db.query(Skill).all()}
    emphasize, gaps = [], []
    for r in opp.requirements or []:
        name = str(r.get("skill", ""))
        s = skills.get(name.lower())
        if s and s.level >= int(r.get("level", 3)):
            emphasize.append({"skill": name, "evidence": s.evidence or "(add concrete evidence)"})
        else:
            gaps.append({"skill": name, "have": s.level if s else 0, "need": int(r.get("level", 3)),
                         "required": bool(r.get("required", True))})
    return {
        "opportunity": f"{opp.title} @ {opp.organization}",
        "deadline": opp.deadline.isoformat() if opp.deadline else None,
        "headline_suggestion": f"{opp.title} candidate — " + ", ".join(e["skill"] for e in emphasize[:3]),
        "emphasize": emphasize,
        "address_gaps": gaps,
        "checklist": [
            "Mirror the posting's key terms in summary and top bullets (truthfully).",
            "One quantified achievement per emphasized skill.",
            "Address the top required gap honestly in the cover letter (plan / transferable evidence).",
            "Proofread; export PDF; YOU submit via the employer's channel.",
        ],
        "note": "Prepared draft only. APEX never submits applications.",
    }


EXECUTORS: dict[str, tuple[Level, Callable[[Session, dict], dict], Callable | None]] = {
    "add_plan_item": (Level.EXECUTE_SAFE, _add_plan_item, _undo_add_plan_item),
    "prepare_cv_tailoring": (Level.PREPARE, _prepare_cv_tailoring, None),
}
SAFE_ACTION_TYPES = frozenset(k for k, (lvl, _, undo) in EXECUTORS.items() if lvl == Level.EXECUTE_SAFE and undo)


def required_level(action_type: str) -> Level:
    if action_type in FORBIDDEN_AUTO:
        return Level.REQUIRES_APPROVAL
    if action_type in EXECUTORS:
        return EXECUTORS[action_type][0]
    return Level.RECOMMEND


def is_granted(db: Session, action_type: str) -> bool:
    if action_type in FORBIDDEN_AUTO or action_type not in SAFE_ACTION_TYPES:
        return False
    g = db.query(AutonomyGrant).filter_by(action_type=action_type, active=True).first()
    return g is not None


def grant(db: Session, action_type: str, active: bool = True) -> None:
    if active and (action_type in FORBIDDEN_AUTO or action_type not in SAFE_ACTION_TYPES):
        raise AutonomyError(f"'{action_type}' cannot be pre-authorized: not a reversible safe action")
    g = db.query(AutonomyGrant).filter_by(action_type=action_type).first()
    if g:
        g.active = active
    else:
        db.add(AutonomyGrant(action_type=action_type, active=active))
    audit(db, "user", "autonomy.grant" if active else "autonomy.revoke", action_type)


def _today_count(db: Session, agent: str) -> int:
    tz = get_settings().tz
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return db.query(AgentAction).filter(AgentAction.agent == agent, AgentAction.created_at >= start).count()


def propose(db: Session, agent: str, action_type: str, payload: dict, idempotency_key: str,
            reversible: bool = True) -> AgentAction:
    """Route an agent's action through the gate. Idempotent per key."""
    existing = db.query(AgentAction).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    if _today_count(db, agent) >= get_settings().agent_daily_action_cap:
        act = AgentAction(agent=agent, action_type=action_type, level=int(required_level(action_type)),
                          status="blocked", reversible=reversible, payload=payload,
                          result={"reason": "daily action cap reached (runaway guard)"},
                          idempotency_key=idempotency_key)
        db.add(act)
        audit(db, f"agent:{agent}", "action.blocked", action_type, reason="cap")
        return act

    level = required_level(action_type)
    act = AgentAction(agent=agent, action_type=action_type, level=int(level), status="proposed",
                      reversible=reversible, payload=payload, idempotency_key=idempotency_key)
    db.add(act)
    db.flush()
    if level == Level.REQUIRES_APPROVAL:
        act.status = "awaiting_approval"
    elif level == Level.PREPARE:
        _run(db, act)
        if act.status == "executed":
            act.status = "prepared"
    elif level == Level.EXECUTE_SAFE:
        if reversible and is_granted(db, action_type):
            _run(db, act)
        else:
            act.status = "awaiting_approval"
    audit(db, f"agent:{agent}", f"action.{act.status}", action_type, action_id=act.id, level=int(level))
    return act


def _run(db: Session, act: AgentAction) -> None:
    if act.action_type in FORBIDDEN_AUTO:  # defense in depth
        raise AutonomyError("forbidden action cannot be executed automatically")
    _, fn, _ = EXECUTORS[act.action_type]
    try:
        act.result = fn(db, act.payload)
        act.status = "executed"
    except Exception as exc:  # executor failures must not crash the cycle
        act.result = {"error": str(exc)[:300]}
        act.status = "failed"
    act.decided_at = datetime.now(timezone.utc)


def approve(db: Session, action_id: int) -> AgentAction:
    act = db.get(AgentAction, action_id)
    if not act:
        raise KeyError(action_id)
    if act.status not in ("awaiting_approval", "proposed", "prepared"):
        raise AutonomyError(f"action is {act.status}")
    if act.action_type in FORBIDDEN_AUTO or act.action_type not in EXECUTORS:
        # No external executor: approval records the user's decision; the user performs it.
        act.status = "approved"
        act.result = {**(act.result or {}), "note": "Approved. Perform this yourself — APEX does not act externally."}
        act.decided_at = datetime.now(timezone.utc)
    elif act.status == "prepared":
        act.status = "approved"
        act.decided_at = datetime.now(timezone.utc)
    else:
        _run(db, act)
    audit(db, "user", "action.approve", act.action_type, action_id=act.id)
    return act


def reject(db: Session, action_id: int) -> AgentAction:
    act = db.get(AgentAction, action_id)
    if not act:
        raise KeyError(action_id)
    act.status = "rejected"
    act.decided_at = datetime.now(timezone.utc)
    audit(db, "user", "action.reject", act.action_type, action_id=act.id)
    return act


def undo(db: Session, action_id: int) -> AgentAction:
    act = db.get(AgentAction, action_id)
    if not act or act.status != "executed" or not act.reversible:
        raise AutonomyError("only executed reversible actions can be undone")
    _, _, undo_fn = EXECUTORS.get(act.action_type, (None, None, None))
    if not undo_fn:
        raise AutonomyError("no undo available")
    undo_fn(db, act.result)
    act.status = "reverted"
    audit(db, "user", "action.undo", act.action_type, action_id=act.id)
    return act
