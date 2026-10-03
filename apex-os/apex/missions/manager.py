"""Mission lifecycle: creation (incl. automatic missions per goal), scheduling, budgets, supervision."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import memory
from ..audit import audit, publish
from ..config import get_settings
from ..integrations import store
from ..models import Goal, Mission, MissionRun
from .roles import DOMAIN_ROLE, ROLES

store.DEFAULTS.setdefault("missions", {"enabled": True, "autonomy": "autonomous", "auto_from_goals": True})
LIVE = ("active", "needs_user")


def settings(db: Session) -> dict:
    cfg = store.get(db, "missions")
    cfg["enabled"] = bool(cfg.get("enabled", True)) and get_settings().missions_enabled
    return cfg


def create(db: Session, *, title: str, role: str, objective: str, criteria: list[str], cadence_hours: int | None = None,
           priority: int = 3, goal_id: int | None = None, created_by: str = "user",
           parent_id: int | None = None) -> Mission:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    if db.query(Mission).filter(Mission.status.in_(LIVE)).count() >= get_settings().max_active_missions:
        raise ValueError("active mission limit reached; pause or finish one first")
    if created_by.startswith("mission:") and parent_id:
        parent = db.get(Mission, parent_id)
        if parent and parent.parent_id:  # one level of delegation only - no runaway agent trees
            raise ValueError("sub-missions cannot create further missions")
    m = Mission(title=title[:200], role=role, objective=objective[:4000],
                success_criteria=[c[:300] for c in criteria if c.strip()][:10] or ["Objective demonstrably met"],
                cadence_hours=max(6, min(int(cadence_hours or ROLES[role].cadence_hours), 336)),
                priority=max(1, min(int(priority), 5)), goal_id=goal_id, created_by=created_by,
                parent_id=parent_id, next_run_at=datetime.now(timezone.utc))
    db.add(m)
    db.flush()
    audit(db, created_by, "mission.create", f"mission:{m.id}", role=role)
    publish(db, "mission.created", id=m.id)
    return m


def ensure_missions(db: Session) -> list[Mission]:
    """Every active goal gets an autonomous mission; a Chief of Staff coordinates when 2+ missions exist."""
    created = []
    cfg = settings(db)
    if not cfg["enabled"]:
        return created
    if cfg.get("auto_from_goals", True):
        for g in db.query(Goal).filter_by(status="active").all():
            if db.query(Mission).filter_by(goal_id=g.id).first():
                continue
            try:
                created.append(create(
                    db, title=g.title, role=DOMAIN_ROLE.get(g.domain, "generalist"),
                    objective=f"Achieve the user's goal: {g.title}." + (f" Why it matters: {g.why}" if g.why else "")
                    + (f" Target: {g.metric} = {g.target_value}." if g.metric and g.target_value else "")
                    + (f" Deadline: {g.deadline}." if g.deadline else ""),
                    criteria=[f"{g.metric} reaches {g.target_value}" if g.metric and g.target_value else
                              f"Measurable, verified progress toward: {g.title}"],
                    priority=g.weight, goal_id=g.id, created_by="auto:goal"))
            except ValueError:
                break
    live = db.query(Mission).filter(Mission.status.in_(LIVE)).count()
    if live >= 2 and not db.query(Mission).filter_by(role="chief_of_staff").filter(
            Mission.status.in_(LIVE + ("paused",))).first():
        created.append(create(
            db, title="Chief of Staff", role="chief_of_staff",
            objective="Coordinate all missions so the user's goals advance with maximum verified progress per "
                      "sustainable hour; keep workload sustainable; surface only decisions that need the user.",
            criteria=["All missions show verified progress or have been re-planned", "Sustainability not degraded"],
            priority=5, created_by="auto:system"))
    return created


def tokens_today(db: Session) -> int:
    start = datetime.now(get_settings().tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    v = db.query(func.coalesce(func.sum(MissionRun.input_tokens + MissionRun.output_tokens), 0)).filter(
        MissionRun.created_at >= start).scalar()
    return int(v or 0)


def due(db: Session, now: datetime | None = None) -> list[Mission]:
    now = now or datetime.now(timezone.utc)
    out = []
    for m in db.query(Mission).filter_by(status="active").all():
        nxt = m.next_run_at
        if nxt is None or (nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc)) <= now:
            out.append(m)
    return sorted(out, key=lambda m: (-m.priority, m.last_run_at or datetime.min.replace(tzinfo=timezone.utc)))


def answer(db: Session, mission_id: int, text: str) -> Mission:
    m = db.get(Mission, mission_id)
    if not m:
        raise KeyError(mission_id)
    memory.remember(db, "long_term", f"mission:{m.id}", f"USER ANSWER: {text[:2000]}", "user", confidence=1.0)
    if m.status == "needs_user":
        m.status = "active"
        m.blocked_reason = ""
        m.next_run_at = datetime.now(timezone.utc)
    audit(db, "user", "mission.answer", f"mission:{m.id}")
    return m


def set_status(db: Session, mission_id: int, status: str) -> Mission:
    m = db.get(Mission, mission_id)
    if not m or status not in ("active", "paused", "stopped"):
        raise KeyError(mission_id)
    m.status = status
    if status == "active":
        m.next_run_at = datetime.now(timezone.utc)
    audit(db, "user", f"mission.{status}", f"mission:{m.id}")
    return m


def next_after(m: Mission) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=m.cadence_hours)
