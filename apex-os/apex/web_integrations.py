"""UI routes for integrations, LinkedIn, comms and the calendar feed."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from . import autonomy
from .audit import audit, publish
from .agents.orchestrator import Orchestrator
from .db import get_db
from .engines import radar
from .integrations import calendar_ics, health_import, llm, mail_imap, notify, radar_sync, store
from .integrations import jobs as jobs_src
from .models import CareerOpportunity, LinkedInProfile, MailItem, Skill
from .web import Auth, back, render, today

router = APIRouter()


def _lines(text: str, limit: int = 30) -> list[str]:
    return [x.strip() for x in text.splitlines() if x.strip()][:limit]


@router.post("/settings/integrations/{key}")
async def save_integration(key: str, request: Request, user=Auth, db: Session = Depends(get_db)):
    f = await request.form()
    if key == "job_sources":
        store.put(db, key, {"feeds": [u for u in _lines(str(f.get("feeds", ""))) if u.startswith("https://")],
                            "arbeitnow": f.get("arbeitnow") == "on",
                            "keywords": _lines(str(f.get("keywords", "")).replace(",", "\n")),
                            "max_per_sync": max(1, min(int(f.get("max_per_sync") or 25), 100))})
    elif key == "radar":
        feeds = []
        for line in _lines(str(f.get("feeds", ""))):
            cat, _, url = line.partition("|") if "|" in line else ("TOOLS", "", line)
            if url.strip().startswith("https://"):
                feeds.append({"url": url.strip(), "category": cat.strip().upper() if cat.strip().upper()
                              in radar.CATEGORIES else "TOOLS"})
        store.put(db, key, {"feeds": feeds, "web_queries": _lines(str(f.get("web_queries", "")), 5),
                            "keywords": _lines(str(f.get("keywords", "")).replace(",", "\n"))})
    elif key == "mail":
        store.put(db, key, {"days": max(1, min(int(f.get("days") or 7), 60))})
    elif key == "notifications":
        store.put(db, key, {"enabled": f.get("enabled") == "on"})
    else:
        raise HTTPException(404)
    audit(db, "user", "integration.configure", key)
    db.commit()
    return back("/settings?msg=Saved")


SYNCS = {"jobs": jobs_src.sync, "radar": radar_sync.sync, "calendar": calendar_ics.sync, "mail": mail_imap.sync,
         "notify": notify.deliver}


@router.post("/settings/sync/{name}")
def sync_now(name: str, user=Auth, db: Session = Depends(get_db)):
    fn = SYNCS.get(name)
    if not fn:
        raise HTTPException(404)
    res = fn(db)
    msg = f"{name}: {res}"[:250]
    return back("/settings?msg=" + msg.replace(" ", "+").replace("&", "and").replace("#", ""))


@router.post("/settings/import/apple-health")
async def apple_health(request: Request, user=Auth, db: Session = Depends(get_db)):
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "file"):
        return back("/settings?msg=Choose+export.xml")
    try:
        res = health_import.import_apple_health(db, up.file)
    except Exception as exc:  # malformed XML etc.
        db.rollback()
        return back(f"/settings?msg=Import+failed:+{type(exc).__name__}")
    return back("/settings?msg=" + f"Apple Health imported: {res}".replace(" ", "+"))


# ------------------------------------------------------------------ LinkedIn
@router.get("/linkedin", response_class=HTMLResponse)
def linkedin_page(request: Request, user=Auth, db: Session = Depends(get_db)):
    p = db.query(LinkedInProfile).order_by(LinkedInProfile.id.desc()).first()
    rep = Orchestrator().plan(db, today()).reports["linkedin"]
    return render(request, "linkedin.html", db=db, p=p, rep=rep)


@router.post("/linkedin")
def linkedin_save(headline: str = Form(""), about: str = Form(""), experience: str = Form(""),
                  skills_text: str = Form(""), activity_posts_90d: int = Form(0), user=Auth,
                  db: Session = Depends(get_db)):
    p = db.query(LinkedInProfile).order_by(LinkedInProfile.id.desc()).first() or LinkedInProfile()
    p.headline, p.about = headline[:300], about[:5000]
    p.experience, p.skills_text = experience[:10000], skills_text[:3000]
    p.activity_posts_90d = max(0, min(activity_posts_90d, 500))
    p.updated_at = datetime.now(timezone.utc)
    db.add(p)
    publish(db, "linkedin.changed")
    db.commit()
    return back("/linkedin")


@router.post("/linkedin/draft")
def linkedin_draft(user=Auth, db: Session = Depends(get_db)):
    p = db.query(LinkedInProfile).order_by(LinkedInProfile.id.desc()).first()
    if not p:
        raise HTTPException(400, "save your profile snapshot first")
    autonomy.propose(db, "linkedin", "draft_linkedin_update", {"profile_id": p.id},
                     idempotency_key=f"linkedin:draft:{datetime.now(timezone.utc).isoformat()}")
    db.commit()
    return back("/actions")


# ------------------------------------------------------------------ career helpers
@router.post("/career/{oid}/extract")
def career_extract(oid: int, user=Auth, db: Session = Depends(get_db)):
    o = db.get(CareerOpportunity, oid)
    if not o:
        raise HTTPException(404)
    skills = [s.name for s in db.query(Skill).all()]
    try:
        reqs = llm.extract_requirements(db, o.description or o.title, skills)
    except llm.LLMUnavailable:
        reqs = jobs_src.heuristic_requirements(f"{o.title} {o.description}", skills)
    if reqs:
        o.requirements = reqs
    publish(db, "career.changed")
    db.commit()
    return back("/career")


@router.post("/career/{oid}/prepare")
def career_prepare(oid: int, user=Auth, db: Session = Depends(get_db)):
    if not db.get(CareerOpportunity, oid):
        raise HTTPException(404)
    autonomy.propose(db, "career", "prepare_cv_tailoring", {"opportunity_id": oid},
                     idempotency_key=f"career:prepare:{oid}:{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
    db.commit()
    return back("/actions")


# ------------------------------------------------------------------ comms
@router.get("/comms", response_class=HTMLResponse)
def comms(request: Request, user=Auth, db: Session = Depends(get_db)):
    mails = db.query(MailItem).order_by(MailItem.received_at.desc()).limit(100).all()
    rep = Orchestrator().plan(db, today()).reports["comms"]
    return render(request, "comms.html", db=db, mails=mails, rep=rep)


@router.post("/comms/{mid}/handled")
def comms_handled(mid: int, user=Auth, db: Session = Depends(get_db)):
    m = db.get(MailItem, mid)
    if m:
        m.handled = True
        publish(db, "mail.handled", id=mid)
        db.commit()
    return back("/comms")


# ------------------------------------------------------------------ calendar feed (token, no session)
@router.get("/calendar/{token}.ics")
def calendar_feed(token: str, db: Session = Depends(get_db)):
    if not calendar_ics.token_ok(token):
        raise HTTPException(404)
    return Response(calendar_ics.plan_feed(db, today()), media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=300"})


