"""Supervision UI for autonomous missions."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .audit import audit
from .config import get_settings
from .db import get_db, session_scope
from .integrations import claude, store
from .missions import manager, runner
from .missions.roles import ROLES
from .models import Goal, Mission, MissionRun
from .web import Auth, back, render

router = APIRouter()


def _run_in_background(mission_id: int) -> None:
    with session_scope() as db:
        m = db.get(Mission, mission_id)
        if m and m.status == "active":
            runner.run(db, m)


@router.get("/missions", response_class=HTMLResponse)
def missions(request: Request, user=Auth, db: Session = Depends(get_db)):
    created = manager.ensure_missions(db)
    if created:
        db.commit()
    ms = db.query(Mission).order_by(Mission.status, Mission.priority.desc(), Mission.id).all()
    return render(request, "missions.html", db=db, missions=ms, roles=ROLES, cfg=manager.settings(db),
                  tokens_today=manager.tokens_today(db), settings=get_settings(), key_ok=claude.available(),
                  goals=db.query(Goal).filter_by(status="active").all())


@router.post("/missions/settings")
async def missions_settings(request: Request, user=Auth, db: Session = Depends(get_db)):
    f = await request.form()
    store.put(db, "missions", {"enabled": f.get("enabled") == "on",
                               "autonomy": "autonomous" if f.get("autonomy") == "autonomous" else "supervised",
                               "auto_from_goals": f.get("auto_from_goals") == "on"})
    audit(db, "user", "missions.settings", str(f.get("autonomy")), enabled=f.get("enabled") == "on")
    db.commit()
    return back("/missions")


@router.post("/missions")
def mission_create(title: str = Form(...), role: str = Form(...), objective: str = Form(...),
                   criteria: str = Form(""), priority: int = Form(3), cadence_hours: int = Form(0),
                   goal_id: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    try:
        manager.create(db, title=title, role=role, objective=objective,
                       criteria=[c.strip() for c in criteria.splitlines() if c.strip()], priority=priority,
                       cadence_hours=cadence_hours or None, goal_id=int(goal_id) if goal_id else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return back("/missions")


@router.get("/missions/{mid}", response_class=HTMLResponse)
def mission_detail(mid: int, request: Request, user=Auth, db: Session = Depends(get_db)):
    m = db.get(Mission, mid)
    if not m:
        raise HTTPException(404)
    runs = db.query(MissionRun).filter_by(mission_id=mid).order_by(MissionRun.id.desc()).limit(20).all()
    from . import memory
    mem = memory.recall(db, category=f"mission:{mid}", limit=30)
    return render(request, "mission.html", db=db, m=m, runs=runs, mem=mem, role=ROLES[m.role],
                  children=db.query(Mission).filter_by(parent_id=mid).all())


@router.post("/missions/{mid}/{op}")
def mission_op(mid: int, op: str, background: BackgroundTasks, answer: str = Form(""), user=Auth,
               db: Session = Depends(get_db)):
    try:
        if op in ("active", "paused", "stopped"):
            manager.set_status(db, mid, op)
        elif op == "answer" and answer.strip():
            manager.answer(db, mid, answer)
        elif op == "run":
            m = db.get(Mission, mid)
            if not m or m.status != "active":
                raise KeyError(mid)
            if not claude.available():
                raise HTTPException(400, "ANTHROPIC_API_KEY not configured")
            background.add_task(_run_in_background, mid)
        else:
            raise HTTPException(400)
    except KeyError:
        raise HTTPException(404) from None
    db.commit()
    return back(f"/missions/{mid}")
