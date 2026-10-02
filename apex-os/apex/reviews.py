"""Morning Brief, Evening Review, Weekly Strategic Review, Monthly Executive Review."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from .agents.orchestrator import CyclePlan, Orchestrator
from .audit import audit
from .models import (
    CareerOpportunity, DailyReview, Experiment, InboxItem, MonthlyReview, PlanItem, WeeklyReview,
)


def _avoid(plan: CyclePlan) -> str:
    s = plan.sustainability
    att = plan.reports["attention"].status.metrics
    if s.band in ("OVERLOADED", "CRITICAL"):
        return f"Adding work. Sustainability is {s.band.lower()} ({', '.join(s.drivers) or 'see dashboard'})."
    if att.get("phone_7d") and att["phone_7d"] > 150:
        return f"Phone before your first focus block (avg {att['phone_7d']:.0f} min/day)."
    prod = plan.reports["productivity"].status.metrics
    if prod.get("realism") is not None and prod["realism"] < 0.7:
        return "Overplanning. Finish the #1 priority before adding anything."
    return "Starting the day with low-value tasks. Do #1 first."


def morning_brief(db: Session, day: date, plan: CyclePlan | None = None, store: bool = True) -> dict:
    plan = plan or Orchestrator().run_cycle(db, day)
    actions = plan.admitted
    learning = next((a for a in actions if a.action.domain in ("german", "law")), None)
    body = next((a for a in actions if a.action.domain in ("recovery", "fitness")), None)
    deadlines = sorted([sg for sg in plan.signals if sg.deadline and sg.deadline >= day], key=lambda sg: sg.deadline)
    leads = plan.reports["career"].extras.get("opportunities", [])
    brief = {
        "day": day.isoformat(),
        "priority": _fmt(actions[0]) if actions else None,
        "top3": [_fmt(a) for a in actions[:3]],
        "learning_target": _fmt(learning) if learning else None,
        "movement_recovery": _fmt(body) if body else None,
        "deadline": ({"title": deadlines[0].title, "deadline": deadlines[0].deadline.isoformat()}
                     if deadlines else None),
        "career": ({"title": f"{leads[0]['title']} @ {leads[0]['organization']}", "match": leads[0]["match"],
                    "action": leads[0]["required_action"]} if leads else None),
        "avoid": _avoid(plan),
        "sustainability": {"index": plan.sustainability.index, "band": plan.sustainability.band,
                           "capacity_min": plan.capacity_min},
    }
    if store:
        r = db.query(DailyReview).filter_by(day=day).first() or DailyReview(day=day)
        r.morning_brief = brief
        db.add(r)
        db.commit()
    return brief


def _fmt(a) -> dict:
    return {"title": a.action.title, "minutes": a.action.minutes, "domain": a.action.domain,
            "score": a.score, "note": a.note, "detail": a.action.detail}


def evening_summary(db: Session, day: date) -> dict:
    items = db.query(PlanItem).filter_by(day=day).all()
    return {
        "planned": [{"title": p.title, "minutes": p.planned_min} for p in items],
        "done": [{"title": p.title, "minutes": p.actual_min or p.planned_min} for p in items if p.status == "done"],
        "partial": [{"title": p.title, "minutes": p.actual_min} for p in items if p.status == "partial"],
        "failed": [{"title": p.title, "reason": p.fail_reason} for p in items if p.status in ("skipped", "planned")],
    }


def save_evening(db: Session, day: date, why_failed: str, learned: str, change_tomorrow: str,
                 energy_end: int | None) -> DailyReview:
    r = db.query(DailyReview).filter_by(day=day).first() or DailyReview(day=day)
    r.evening = evening_summary(db, day)
    r.why_failed, r.learned, r.change_tomorrow, r.energy_end = why_failed, learned, change_tomorrow, energy_end
    db.add(r)
    audit(db, "user", "review.evening", day.isoformat())
    db.commit()
    return r


# ------------------------------------------------------------------ weekly / monthly
def _compare(orch: Orchestrator, db: Session, then: date, now: date) -> dict:
    a, b = orch.plan(db, then), orch.plan(db, now)
    out = {}
    for name, rep in b.reports.items():
        old = a.reports[name].status.score
        new = rep.status.score
        out[name] = {"label": rep.status.label, "then": old, "now": new,
                     "delta": round(new - old, 1) if old is not None and new is not None else None,
                     "headline": rep.status.headline, "so_what": rep.status.so_what}
    out["_sustainability"] = {"then": a.sustainability.index, "now": b.sustainability.index}
    out["_plan"] = b
    return out


def weekly_review(db: Session, week_end: date, store: bool = True) -> dict:
    orch = Orchestrator()
    cmp = _compare(orch, db, week_end - timedelta(days=7), week_end)
    plan: CyclePlan = cmp.pop("_plan")
    susd = cmp.pop("_sustainability")
    keep, stop, start, inc, dec, exp = [], [], [], [], [], []
    for name, d in cmp.items():
        if d["delta"] is not None and d["delta"] >= 3:
            keep.append(f"{d['label']} {d['delta']:+.0f} ({d['headline']}) — keep the current approach")
        elif d["delta"] is not None and d["delta"] <= -3:
            stop.append(f"{d['label']} {d['delta']:+.0f} ({d['headline']}) — stop whatever changed this week")
        if d["now"] is None and name not in ("learning",):
            start.append(f"Start measuring {d['label']}")
    s = plan.sustainability
    if s.protective:
        dec.append(f"Total workload — sustainability {s.band.lower()} ({s.index})")
    elif s.index is not None and s.index >= 75 and plan.bottleneck:
        inc.append(f"{plan.bottleneck}: capacity available, invest in the bottleneck")
    for sg in plan.reports["learning"].extras.get("experiment_suggestions", [])[:2]:
        exp.append(sg["hypothesis"])
    prod = plan.reports["productivity"].status.metrics
    if prod.get("realism") is not None and prod["realism"] < 0.75:
        dec.append(f"Planned volume — you execute {prod['realism']:.0%} of it")
    german = plan.reports["german"].status.metrics
    if german.get("active_ratio") is not None and german["active_ratio"] < 0.5:
        inc.append("German active practice share (speaking/writing)")
    content = {
        "week_end": week_end.isoformat(),
        "domains": cmp,
        "sustainability": susd,
        "KEEP": keep, "STOP": stop, "START": start, "INCREASE": inc, "DECREASE": dec, "EXPERIMENT": exp,
        "bottleneck": plan.bottleneck,
    }
    if store:
        ws = week_end - timedelta(days=6)
        r = db.query(WeeklyReview).filter_by(week_start=ws).first() or WeeklyReview(week_start=ws)
        r.content = content
        db.add(r)
        db.commit()
    return content


def monthly_review(db: Session, month_end: date, store: bool = True) -> dict:
    orch = Orchestrator()
    cmp = _compare(orch, db, month_end - timedelta(days=30), month_end)
    plan: CyclePlan = cmp.pop("_plan")
    susd = cmp.pop("_sustainability")
    improved = [f"{d['label']} {d['delta']:+.0f}" for d in cmp.values() if d["delta"] is not None and d["delta"] >= 3]
    not_improved = [f"{d['label']} {d['delta']:+.0f}" for d in cmp.values()
                    if d["delta"] is not None and d["delta"] < 3]
    leads = plan.reports["career"].extras.get("opportunities", [])
    evaluated = db.query(Experiment).filter(Experiment.status == "evaluated").all()
    content = {
        "month": month_end.strftime("%Y-%m"),
        "where_i_was": {k: d["then"] for k, d in cmp.items()} | {"sustainability": susd["then"]},
        "where_i_am": {k: d["now"] for k, d in cmp.items()} | {"sustainability": susd["now"]},
        "improved": improved,
        "did_not_improve": not_improved,
        "bottleneck": plan.bottleneck,
        "opportunity": (f"{leads[0]['title']} @ {leads[0]['organization']} ({leads[0]['match']:.0%})"
                        if leads else None),
        "experiments": [{"hypothesis": e.hypothesis, "decision": e.decision, "result": e.result} for e in evaluated],
        "next_30_days": [a.action.title for a in plan.admitted[:3]],
    }
    if store:
        r = db.query(MonthlyReview).filter_by(month=content["month"]).first() or MonthlyReview(month=content["month"])
        r.content = content
        db.add(r)
        db.commit()
    return content


def upcoming_deadlines(db: Session, day: date, days: int = 14) -> list[dict]:
    out = []
    for o in db.query(CareerOpportunity).filter(CareerOpportunity.deadline.isnot(None),
                                                CareerOpportunity.status.in_(("new", "shortlisted"))).all():
        if day <= o.deadline <= day + timedelta(days=days):
            out.append({"title": f"{o.title} @ {o.organization}", "deadline": o.deadline})
    for i in db.query(InboxItem).filter(InboxItem.deadline.isnot(None), InboxItem.status != "done").all():
        if day <= i.deadline <= day + timedelta(days=days) and not any(x["title"] in i.title for x in out):
            out.append({"title": i.title, "deadline": i.deadline})
    return sorted(out, key=lambda x: x["deadline"])
