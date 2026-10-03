"""Bridge between the Mission Control artifact's store and the deterministic APEX engine.

Runtime on a Claude Pro plan (no server, no API key):
  artifact db (system of record, private)  --export JSON-->  `python -m apex bridge`
  -> fresh in-memory DB rebuilt from raw events (raw-events-first)
  -> orchestrator + engines (deterministic numbers)
  -> projection JSON  --written back by the Claude Code session-->  artifact db

Input (state.json) mirrors the artifact collections:
  {"today": "YYYY-MM-DD",
   "events": [{"id","ts","kind","data",...}],              # flattened from events/{day} docs
   "goals":  [{"id","title","domain","weight","deadline","why","status"}],
   "skills": [{"name","level","evidence"}],
   "plan":   [{"id","day","title","domain","minutes","status","actual_min","fail_reason"}],
   "opportunities": [{"title","organization","url","deadline","requirements":[...],"source"}]}
Personal data never touches the repository: state and output live in the session's temp dir.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from . import logbook
from .agents.orchestrator import Orchestrator
from .engines import notifications
from .models import CareerOpportunity, Goal, PlanItem, Skill
from .reviews import morning_brief

DOMAIN_LABELS = {"german": "Niemiecki", "law": "Prawo", "career": "Kariera", "fitness": "Ruch",
                 "recovery": "Regeneracja", "attention": "Uwaga", "productivity": "Wykonanie",
                 "learning": "Uczenie się", "research": "Radar", "linkedin": "LinkedIn", "comms": "Poczta"}


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def rebuild(db: Session, state: dict) -> dict:
    """Load raw state into an empty DB. Invalid entries are reported, never guessed."""
    loaded, errors = 0, []
    for e in sorted(state.get("events", []), key=lambda x: x.get("ts", "")):
        kind = e.get("kind")
        try:
            with db.begin_nested():
                logbook.create(db, kind, dict(e.get("data") or {}), source=str(e.get("source") or "manual")[:64])
            loaded += 1
        except (logbook.ValidationError, ValueError, TypeError) as exc:
            errors.append({"id": e.get("id"), "kind": kind, "error": str(exc)[:200]})
    for g in state.get("goals", []):
        if g.get("status", "active") in ("active", "paused", "done", "dropped") and g.get("title"):
            db.add(Goal(title=str(g["title"])[:200], domain=str(g.get("domain") or "other"),
                        weight=max(1, min(int(g.get("weight") or 3), 5)), why=str(g.get("why") or "")[:1000],
                        deadline=_d(g.get("deadline")), status=g.get("status", "active")))
    for s in state.get("skills", []):
        if s.get("name"):
            db.add(Skill(name=str(s["name"])[:128], level=max(0, min(int(s.get("level") or 0), 5)),
                         evidence=str(s.get("evidence") or "")[:1000]))
    for p in state.get("plan", []):
        day = _d(p.get("day"))
        if day and p.get("title"):
            db.add(PlanItem(day=day, title=str(p["title"])[:200], domain=str(p.get("domain") or "other"),
                            planned_min=int(p.get("minutes") or 30), status=p.get("status") or "planned",
                            actual_min=p.get("actual_min"), fail_reason=str(p.get("fail_reason") or "")[:200]))
    for o in state.get("opportunities", []):
        if o.get("title"):
            db.add(CareerOpportunity(
                title=str(o["title"])[:200], organization=str(o.get("organization") or "")[:200],
                url=str(o.get("url") or "")[:500], deadline=_d(o.get("deadline")),
                requirements=o.get("requirements") or [], source=str(o.get("source") or "manual")[:64],
                status=o.get("status") or "new", strategic_fit=int(o.get("strategic_fit") or 3),
                retrieved_at=datetime.now(timezone.utc)))
    db.commit()
    return {"events_loaded": loaded, "errors": errors}


def _action(a) -> dict:
    w = a.action.why
    return {"key": a.action.key, "title": a.action.title, "detail": a.action.detail, "minutes": a.action.minutes,
            "domain": a.action.domain, "score": a.score, "note": a.note,
            "why": {"data_used": w.data_used, "reasoning": w.reasoning, "expected_benefit": w.expected_benefit,
                    "confidence": w.confidence, "alternatives": w.alternatives, "downside": w.downside},
            "criteria": a.criteria}


def snapshot(db: Session, day: date) -> dict:
    """Deterministic projection for Mission Control. No LLM involved."""
    plan = Orchestrator().plan(db, day)
    s = plan.sustainability
    signals = []
    for sg in plan.signals:
        disp = notifications.disposition(sg, day)
        if disp == "IGNORE":
            continue
        signals.append({
            "key": sg.key, "kind": sg.kind, "severity": sg.severity, "title": sg.title, "so_what": sg.so_what,
            "inbox_kind": sg.inbox_kind, "disposition": disp,
            "class": "CRITICAL" if notifications.interrupt_worthy(sg, day) else
                     "IMPORTANT" if sg.severity >= 3 else "FYI",
            "priority": notifications.inbox_priority(sg, day),
            "deadline": sg.deadline.isoformat() if sg.deadline else None,
            "evidence": {"statement": sg.insight.statement, "epistemic": sg.insight.epistemic,
                         "confidence": sg.insight.confidence, "n": sg.insight.n,
                         "data_used": sg.insight.data_used, "caveat": sg.insight.caveat},
        })
    domains = {}
    for name, r in plan.reports.items():
        st = r.status
        domains[name] = {"label": DOMAIN_LABELS.get(name, st.label), "score": st.score, "headline": st.headline,
                         "so_what": st.so_what, "trend": st.trend, "needs": st.needs}
    vel = {}
    for name in ("german", "law"):
        v = plan.reports[name].status.metrics.get("velocity")
        if v:
            vel[name] = {k: v.get(k) for k in ("units_per_100h", "baseline", "current", "hours", "measurements",
                                               "reason")}
    graph = plan.reports["law"].extras.get("knowledge_graph", {})
    return {
        "schema": 1,
        "day": day.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "apex_score": plan.apex_score,
        "apex_explain": plan.apex_explain,
        "bottleneck": plan.bottleneck,
        "sustainability": {"index": s.index, "band": s.band, "capacity_factor": s.capacity_factor,
                           "components": s.components, "drivers": s.drivers,
                           "capacity_min": plan.capacity_min, "capacity_left_min": plan.capacity_left_min},
        "top3": [_action(a) for a in plan.admitted[:3]],
        "next_actions": [_action(a) for a in plan.admitted[3:]],
        "deferred": [{"title": d.action.title, "reason": d.reason, "score": d.score} for d in plan.deferred[:8]],
        "domains": domains,
        "velocity": vel,
        "signals": sorted(signals, key=lambda x: -x["priority"])[:20],
        "law_graph": {a: {"status": g["status"], "accuracy": g["accuracy"], "n": g["evidence_n"]}
                      for a, g in graph.items()},
        "brief": morning_brief(db, day, plan=plan, store=False),
        "twin": plan.reports["learning"].extras.get("twin", []),
    }


def run(state: dict) -> dict:
    """Rebuild + snapshot in one call (the CLI uses an in-memory DB)."""
    from .db import new_session

    db = new_session()
    try:
        day = _d(state.get("today")) or date.today()
        load = rebuild(db, state)
        out = snapshot(db, day)
        out["load"] = load
        out["coverage"] = _coverage(state)
        return out
    finally:
        db.close()


def _coverage(state: dict) -> dict:
    kinds: dict[str, int] = {}
    last = None
    for e in state.get("events", []):
        kinds[e.get("kind", "?")] = kinds.get(e.get("kind", "?"), 0) + 1
        last = max(last or "", str(e.get("ts", "")))
    return {"events": sum(kinds.values()), "by_kind": kinds, "last_entry": last or None,
            "goals": len(state.get("goals", []))}


def main(in_path: str, out_path: str) -> dict:
    with open(in_path, encoding="utf-8") as fh:
        state = json.load(fh)
    out = run(state)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=str)
    return {"events_loaded": out["load"]["events_loaded"], "errors": len(out["load"]["errors"]),
            "top3": [a["title"] for a in out["top3"]]}
