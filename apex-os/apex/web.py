"""HTML UI. Server-rendered, mobile-first, no build step."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import autonomy, logbook, memory, reviews, security
from .agents.orchestrator import Orchestrator
from .audit import audit, publish
from .config import get_settings
from .db import get_db
from .dataops import export_all, wipe_all
from .engines import experiments as xp
from .engines import radar
from .integrations import store as istore
from .integrations.registry import statuses
from .models import (
    AgentAction, AgentMemory, Application, AutonomyGrant, CareerOpportunity, DailyReview, Experiment, Goal,
    InboxItem, PlanItem, Recommendation, ResearchItem, Skill, User,
)
from .untrusted import sanitize

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))
router = APIRouter()
Auth = Depends(security.current_user)


def today() -> date:
    return datetime.now(get_settings().tz).date()


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    db: Session | None = ctx.pop("db", None)
    if db is not None:
        ctx.setdefault("inbox_unread", db.query(InboxItem).filter_by(status="unread").count())
        ctx.setdefault("approvals", db.query(AgentAction).filter(
            AgentAction.status.in_(("awaiting_approval", "prepared"))).count())
    ctx.setdefault("today", today())
    return TEMPLATES.TemplateResponse(request, name, ctx)


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ auth
@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if db.query(User).count():
        return back("/login")
    return render(request, "setup.html")


@router.post("/setup")
def setup(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).count():
        raise HTTPException(403, "owner already exists")
    try:
        security.create_user(db, username.strip(), password)
    except ValueError as exc:
        return render(request, "setup.html", error=str(exc))
    audit(db, "user", "auth.setup", username)
    db.commit()
    return back("/login")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if not db.query(User).count():
        return back("/setup")
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    token = security.login(db, username, password)
    audit(db, "user" if token else "anonymous", "auth.login" if token else "auth.login_failed", username)
    db.commit()
    if not token:
        return render(request, "login.html", error="Invalid credentials")
    resp = back("/")
    resp.set_cookie(security.COOKIE, token, httponly=True, samesite="strict", secure=get_settings().cookie_secure,
                    max_age=get_settings().session_days * 86400)
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    tok = request.cookies.get(security.COOKIE)
    if tok:
        security.logout(db, tok)
    resp = back("/login")
    resp.delete_cookie(security.COOKIE)
    return resp


# ------------------------------------------------------------------ home / brief
@router.get("/", response_class=HTMLResponse)
def home(request: Request, user=Auth, db: Session = Depends(get_db)):
    d = today()
    plan = Orchestrator().run_cycle(db, d)
    recs = {r.key: r for r in db.query(Recommendation).filter_by(day=d).all()}
    alerts = (db.query(InboxItem).filter(InboxItem.status == "unread", InboxItem.priority >= 70)
              .order_by(InboxItem.priority.desc()).limit(4).all())
    exps = db.query(Experiment).filter_by(status="running").all()
    return render(request, "home.html", db=db, plan=plan, recs=recs, alerts=alerts, experiments=exps,
                  deadlines=reviews.upcoming_deadlines(db, d), user=user)


@router.post("/recommendations/{rid}/{op}")
def recommendation_op(rid: int, op: str, user=Auth, db: Session = Depends(get_db)):
    r = db.get(Recommendation, rid)
    if not r:
        raise HTTPException(404)
    if op == "accept":
        if not db.query(PlanItem).filter_by(day=r.day, recommendation_id=r.id).first():
            db.add(PlanItem(day=r.day, title=r.title, domain=r.domain, planned_min=r.minutes or 15,
                            recommendation_id=r.id))
        r.status = "accepted"
    elif op == "dismiss":
        r.status = "dismissed"
    else:
        raise HTTPException(400)
    audit(db, "user", f"recommendation.{op}", f"rec:{rid}")
    publish(db, "plan.changed", rec=rid)
    db.commit()
    return back("/")


@router.get("/brief", response_class=HTMLResponse)
def brief(request: Request, user=Auth, db: Session = Depends(get_db)):
    b = reviews.morning_brief(db, today())
    return render(request, "brief.html", db=db, brief=b)


# ------------------------------------------------------------------ plan
@router.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request, user=Auth, db: Session = Depends(get_db)):
    d = today()
    items = db.query(PlanItem).filter_by(day=d).order_by(PlanItem.id).all()
    return render(request, "plan.html", db=db, items=items, domains=logbook.DOMAINS)


@router.post("/plan")
def plan_add(title: str = Form(...), domain: str = Form("other"), minutes: int = Form(30), user=Auth,
             db: Session = Depends(get_db)):
    db.add(PlanItem(day=today(), title=title[:200], domain=domain, planned_min=max(0, min(minutes, 600))))
    publish(db, "plan.changed")
    db.commit()
    return back("/plan")


@router.post("/plan/{pid}")
def plan_update(pid: int, status: str = Form(...), actual_min: str = Form(""), quality: str = Form(""),
                fail_reason: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    p = db.get(PlanItem, pid)
    if not p or status not in ("planned", "done", "partial", "skipped"):
        raise HTTPException(400)
    p.status = status
    p.actual_min = int(actual_min) if actual_min.strip() else None
    p.quality = int(quality) if quality.strip() else None
    p.fail_reason = fail_reason[:200]
    p.completed_at = datetime.now(timezone.utc) if status in ("done", "partial") else None
    if p.recommendation_id and status == "done":
        r = db.get(Recommendation, p.recommendation_id)
        if r:
            r.status = "done"
    publish(db, "plan.changed", item=pid)
    db.commit()
    return back("/plan")


# ------------------------------------------------------------------ logging
@router.get("/log", response_class=HTMLResponse)
def log_page(request: Request, kind: str = "german_session", user=Auth, db: Session = Depends(get_db)):
    if kind not in logbook.SPECS:
        kind = "german_session"
    return render(request, "log.html", db=db, specs=logbook.SPECS, kind=kind, saved=request.query_params.get("saved"))


@router.post("/log/{kind}")
async def log_submit(kind: str, request: Request, user=Auth, db: Session = Depends(get_db)):
    form = dict(await request.form())
    try:
        logbook.create(db, kind, form)
        db.commit()
    except logbook.ValidationError as exc:
        db.rollback()
        return render(request, "log.html", db=db, specs=logbook.SPECS, kind=kind, error=str(exc), values=form)
    return back(f"/log?kind={kind}&saved=1")


# ------------------------------------------------------------------ inbox / actions
@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, show: str = "open", user=Auth, db: Session = Depends(get_db)):
    q = db.query(InboxItem)
    if show == "open":
        q = q.filter(InboxItem.status.in_(("unread", "read")))
    items = q.order_by(InboxItem.status.desc(), InboxItem.priority.desc(), InboxItem.created_at.desc()).limit(100).all()
    return render(request, "inbox.html", db=db, items=items, show=show)


@router.post("/inbox/{iid}/{status}")
def inbox_status(iid: int, status: str, user=Auth, db: Session = Depends(get_db)):
    i = db.get(InboxItem, iid)
    if not i or status not in ("read", "dismissed", "done", "unread"):
        raise HTTPException(400)
    i.status = status
    db.commit()
    return back("/inbox")


@router.get("/actions", response_class=HTMLResponse)
def actions(request: Request, user=Auth, db: Session = Depends(get_db)):
    items = db.query(AgentAction).order_by(AgentAction.created_at.desc()).limit(100).all()
    return render(request, "actions.html", db=db, items=items, forbidden=autonomy.FORBIDDEN_AUTO)


@router.post("/actions/{aid}/{op}")
def action_op(aid: int, op: str, user=Auth, db: Session = Depends(get_db)):
    fn = {"approve": autonomy.approve, "reject": autonomy.reject, "undo": autonomy.undo}.get(op)
    if not fn:
        raise HTTPException(400)
    try:
        fn(db, aid)
    except (autonomy.AutonomyError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None
    publish(db, "action.decided", id=aid, op=op)
    db.commit()
    return back("/actions")


# ------------------------------------------------------------------ reviews
@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, user=Auth, db: Session = Depends(get_db)):
    d = today()
    r = db.query(DailyReview).filter_by(day=d).first()
    return render(request, "review.html", db=db, summary=reviews.evening_summary(db, d), review=r)


@router.post("/review")
def review_save(why_failed: str = Form(""), learned: str = Form(""), change_tomorrow: str = Form(""),
                energy_end: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    reviews.save_evening(db, today(), why_failed[:2000], learned[:2000], change_tomorrow[:2000],
                         int(energy_end) if energy_end.strip() else None)
    if change_tomorrow.strip():
        memory.remember(db, "daily", "change_tomorrow", change_tomorrow[:500], "user", key=today().isoformat(),
                        confidence=1.0, today=today())
    if why_failed.strip():
        memory.remember(db, "learning", "WHAT CAUSES FAILURE", why_failed[:500], "user",
                        key=f"evening:{today().isoformat()}", confidence=0.6, today=today())
    db.commit()
    return back("/review")


@router.get("/review/weekly", response_class=HTMLResponse)
def weekly(request: Request, user=Auth, db: Session = Depends(get_db)):
    return render(request, "weekly.html", db=db, w=reviews.weekly_review(db, today()))


@router.get("/review/monthly", response_class=HTMLResponse)
def monthly(request: Request, user=Auth, db: Session = Depends(get_db)):
    return render(request, "monthly.html", db=db, m=reviews.monthly_review(db, today()))


# ------------------------------------------------------------------ goals / skills
@router.get("/goals", response_class=HTMLResponse)
def goals(request: Request, user=Auth, db: Session = Depends(get_db)):
    return render(request, "goals.html", db=db, goals=db.query(Goal).order_by(Goal.status, Goal.weight.desc()).all(),
                  skills=db.query(Skill).order_by(Skill.name).all(), domains=logbook.DOMAINS)


@router.post("/goals")
def goal_add(title: str = Form(...), domain: str = Form(...), weight: int = Form(3), why: str = Form(""),
             deadline: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    db.add(Goal(title=title[:200], domain=domain, weight=max(1, min(weight, 5)), why=why[:1000],
                deadline=date.fromisoformat(deadline) if deadline else None))
    publish(db, "goals.changed")
    db.commit()
    return back("/goals")


@router.post("/goals/{gid}")
def goal_update(gid: int, status: str = Form(...), user=Auth, db: Session = Depends(get_db)):
    g = db.get(Goal, gid)
    if not g:
        raise HTTPException(404)
    if status == "delete":
        db.delete(g)
    elif status in ("active", "paused", "done", "dropped"):
        g.status = status
    publish(db, "goals.changed")
    db.commit()
    return back("/goals")


@router.post("/skills")
def skill_add(name: str = Form(...), level: int = Form(...), evidence: str = Form(""), user=Auth,
              db: Session = Depends(get_db)):
    s = db.query(Skill).filter(Skill.name.ilike(name.strip())).first() or Skill(name=name.strip()[:128])
    s.level, s.evidence = max(0, min(level, 5)), evidence[:1000]
    db.add(s)
    publish(db, "skills.changed")
    db.commit()
    return back("/goals")


@router.post("/skills/{sid}/delete")
def skill_delete(sid: int, user=Auth, db: Session = Depends(get_db)):
    s = db.get(Skill, sid)
    if s:
        db.delete(s)
        publish(db, "skills.changed")
        db.commit()
    return back("/goals")


# ------------------------------------------------------------------ domain views
@router.get("/german", response_class=HTMLResponse)
def german(request: Request, user=Auth, db: Session = Depends(get_db)):
    rep = Orchestrator().plan(db, today()).reports["german"]
    return render(request, "domain.html", db=db, rep=rep)


@router.get("/law", response_class=HTMLResponse)
def law(request: Request, user=Auth, db: Session = Depends(get_db)):
    rep = Orchestrator().plan(db, today()).reports["law"]
    graph = rep.extras.get("knowledge_graph", {})
    graph = dict(sorted(graph.items(), key=lambda kv: -1 if kv[1]["accuracy"] is None else kv[1]["accuracy"]))
    return render(request, "law.html", db=db, rep=rep, graph=graph)


@router.get("/domain/{name}", response_class=HTMLResponse)
def domain(name: str, request: Request, user=Auth, db: Session = Depends(get_db)):
    plan = Orchestrator().plan(db, today())
    if name not in plan.reports:
        raise HTTPException(404)
    return render(request, "domain.html", db=db, rep=plan.reports[name])


def _parse_requirements(text: str) -> list[dict]:
    """Lines like 'German: 4' or 'GDPR: 3 optional'."""
    reqs = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        parts = rest.split()
        try:
            lvl = max(1, min(int(parts[0]), 5))
        except (ValueError, IndexError):
            lvl = 3
        reqs.append({"skill": name.strip()[:128], "level": lvl,
                     "required": not (len(parts) > 1 and parts[1].lower().startswith("opt"))})
    return reqs[:30]


@router.get("/career", response_class=HTMLResponse)
def career(request: Request, user=Auth, db: Session = Depends(get_db)):
    rep = Orchestrator().plan(db, today()).reports["career"]
    apps = db.query(Application).all()
    return render(request, "career.html", db=db, rep=rep, apps=apps, llm_on=bool(get_settings().openai_api_key))


@router.post("/career")
def career_add(title: str = Form(...), organization: str = Form(""), location: str = Form(""), url: str = Form(""),
               deadline: str = Form(""), requirements: str = Form(""), salary_text: str = Form(""),
               salary_source: str = Form(""), strategic_fit: int = Form(3), description: str = Form(""),
               source: str = Form("manual"), user=Auth, db: Session = Depends(get_db)):
    desc, flags = sanitize(description)
    o = CareerOpportunity(title=title[:200], organization=organization[:200], location=location[:128],
                          url=url[:500], deadline=date.fromisoformat(deadline) if deadline else None,
                          requirements=_parse_requirements(requirements), salary_text=salary_text[:128],
                          salary_source=salary_source[:300], strategic_fit=max(1, min(strategic_fit, 5)),
                          description=desc, injection_flags=flags, source=source[:64] or "manual",
                          retrieved_at=datetime.now(timezone.utc))
    db.add(o)
    publish(db, "career.changed")
    db.commit()
    return back("/career")


@router.post("/career/{oid}/status")
def career_status(oid: int, status: str = Form(...), user=Auth, db: Session = Depends(get_db)):
    o = db.get(CareerOpportunity, oid)
    if not o or status not in ("new", "shortlisted", "applied", "rejected", "archived"):
        raise HTTPException(400)
    o.status = status
    if status == "applied":  # the USER applied; APEX only records it
        db.add(Application(opportunity_id=o.id, status="submitted", submitted_at=today()))
    audit(db, "user", "career.status", f"opp:{oid}", status=status)
    publish(db, "career.changed")
    db.commit()
    return back("/career")


# ------------------------------------------------------------------ experiments / radar
@router.get("/experiments", response_class=HTMLResponse)
def experiments_page(request: Request, user=Auth, db: Session = Depends(get_db)):
    plan = Orchestrator().plan(db, today())
    return render(request, "experiments.html", db=db,
                  items=db.query(Experiment).order_by(Experiment.created_at.desc()).all(),
                  metrics=xp.METRICS, suggestions=plan.reports["learning"].extras.get("experiment_suggestions", []))


@router.post("/experiments")
def experiment_add(request: Request, hypothesis: str = Form(...), intervention: str = Form(...),
                   metric: str = Form(...), domain: str = Form("learning"), days: int = Form(14),
                   user=Auth, db: Session = Depends(get_db)):
    try:
        xp.create(db, hypothesis[:300], domain, intervention[:300], metric, today(), max(6, min(days, 60)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return back("/experiments")


@router.post("/experiments/{eid}/{op}")
def experiment_op(eid: int, op: str, user=Auth, db: Session = Depends(get_db)):
    e = db.get(Experiment, eid)
    if not e:
        raise HTTPException(404)
    if op == "evaluate":
        res = xp.evaluate(db, e, today())
        if res.get("finished") and res.get("insight"):
            ins = res["insight"]
            memory.remember(db, "learning", "WHICH INTERVENTIONS ACTUALLY WORK", ins["statement"],
                            f"experiment:{e.id}", key=f"exp:{e.id}", epistemic=ins["epistemic"],
                            confidence={"low": 0.3, "medium": 0.6, "high": 0.85}[ins["confidence"]], today=today())
            db.add(InboxItem(kind="EXPERIMENT_RESULT", agent="experiments", title=f"Result: {e.hypothesis}"[:200],
                             body=ins["statement"], so_what=f"Decision: {res['decision']}", priority=70,
                             dedupe_key=f"exp:{e.id}@result", why={"insight": ins}))
    elif op == "stop":
        e.status = "stopped"
    db.commit()
    return back("/experiments")


@router.get("/radar", response_class=HTMLResponse)
def radar_page(request: Request, show: str = "pass", user=Auth, db: Session = Depends(get_db)):
    q = db.query(ResearchItem).filter(ResearchItem.status != "dismissed")
    items = q.order_by(ResearchItem.expected_value.desc()).all()
    shown = [i for i in items if show == "all" or radar.passes(i.expected_value)]
    return render(request, "radar.html", db=db, items=shown, hidden=len(items) - len(shown), show=show,
                  categories=radar.CATEGORIES, threshold=radar.THRESHOLD)


@router.post("/radar")
def radar_add(category: str = Form(...), title: str = Form(...), url: str = Form(""), summary: str = Form(""),
              relevance: int = Form(3), impact: int = Form(3), evidence: int = Form(3), actionability: int = Form(3),
              time_cost_h: float = Form(1), money_cost: float = Form(0), user=Auth, db: Session = Depends(get_db)):
    clamp = lambda v: max(1, min(int(v), 5))  # noqa: E731
    text, flags = sanitize(summary)
    ev = radar.expected_value(clamp(relevance), clamp(impact), clamp(evidence), clamp(actionability),
                              max(0, time_cost_h), max(0, money_cost))
    db.add(ResearchItem(category=category if category in radar.CATEGORIES else "TOOLS", title=title[:300],
                        url=url[:500], summary=text, relevance=clamp(relevance), impact=clamp(impact),
                        evidence=clamp(evidence), actionability=clamp(actionability), time_cost_h=time_cost_h,
                        money_cost=money_cost, expected_value=ev, injection_flags=flags,
                        retrieved_at=datetime.now(timezone.utc)))
    db.commit()
    return back("/radar")


@router.post("/radar/{rid}/{status}")
def radar_status(rid: int, status: str, user=Auth, db: Session = Depends(get_db)):
    r = db.get(ResearchItem, rid)
    if r and status in ("kept", "dismissed", "new"):
        r.status = status
        db.commit()
    return back("/radar")


# ------------------------------------------------------------------ memory
@router.get("/memory", response_class=HTMLResponse)
def memory_page(request: Request, layer: str = "", user=Auth, db: Session = Depends(get_db)):
    sweep = memory.sweep(db, today())
    db.commit()
    return render(request, "memory.html", db=db, twin=memory.digital_twin(db),
                  records=memory.recall(db, layer=layer or None, limit=150), layers=memory.LAYERS, layer=layer,
                  due=sweep["due_for_review"])


@router.post("/memory")
def memory_add(layer: str = Form("long_term"), category: str = Form(...), content: str = Form(...), user=Auth,
               db: Session = Depends(get_db)):
    if layer not in memory.LAYERS:
        raise HTTPException(400)
    memory.remember(db, layer, category[:64], content[:2000], "user", confidence=1.0, today=today())
    db.commit()
    return back("/memory")


@router.post("/memory/{mid}/{op}")
def memory_op(mid: int, op: str, content: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    try:
        if op == "correct" and content.strip():
            memory.correct(db, mid, content[:2000])
        elif op == "forget":
            memory.forget(db, mid)
        elif op == "reviewed":
            m = db.get(AgentMemory, mid)
            if m:
                m.review_at = today() + timedelta(days=memory.REVIEW_DAYS.get(m.layer, 90))
    except KeyError:
        raise HTTPException(404) from None
    db.commit()
    return back("/memory")


# ------------------------------------------------------------------ settings / data ops
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Auth, db: Session = Depends(get_db)):
    grants = {g.action_type: g.active for g in db.query(AutonomyGrant).all()}
    return render(request, "settings.html", db=db, integrations=statuses(get_settings(), db),
                  cfg={k: istore.get(db, k) for k in istore.DEFAULTS}, llm_on=bool(get_settings().openai_api_key), grants=grants,
                  safe=sorted(autonomy.SAFE_ACTION_TYPES), forbidden=sorted(autonomy.FORBIDDEN_AUTO),
                  specs=logbook.SPECS, msg=request.query_params.get("msg"), settings=get_settings())


@router.post("/settings/grant")
def settings_grant(action_type: str = Form(...), active: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    try:
        autonomy.grant(db, action_type, active == "on")
    except autonomy.AutonomyError as exc:
        raise HTTPException(400, str(exc)) from None
    publish(db, "autonomy.changed")
    db.commit()
    return back("/settings")


@router.post("/settings/import")
async def settings_import(request: Request, user=Auth, db: Session = Depends(get_db)):
    form = await request.form()
    kind = str(form.get("kind", ""))
    upload = form.get("file")
    text = (await upload.read()).decode("utf-8", "replace") if upload is not None and hasattr(upload, "read") else ""
    text = text or str(form.get("text", ""))
    if kind not in logbook.SPECS or not text.strip():
        return back("/settings?msg=Choose+a+kind+and+provide+CSV")
    res = logbook.import_csv(db, kind, text[:2_000_000])
    msg = f"Imported {res['imported']} rows" + (f"; {len(res['errors'])} errors: {'; '.join(res['errors'][:3])}"
                                                 if res["errors"] else "")
    return back("/settings?msg=" + msg.replace(" ", "+").replace("&", "and"))


@router.get("/settings/export")
def settings_export(user=Auth, db: Session = Depends(get_db)):
    audit(db, "user", "data.export")
    db.commit()
    body = json.dumps(export_all(db), default=str, indent=1, ensure_ascii=False)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="apex-export-{today()}.json"'})


@router.post("/settings/wipe")
def settings_wipe(confirm: str = Form(""), user=Auth, db: Session = Depends(get_db)):
    if confirm != "DELETE ALL MY DATA":
        return back("/settings?msg=Wipe+not+confirmed")
    n = wipe_all(db)
    return back(f"/settings?msg=Deleted+{n}+rows")
