"""EXPERIMENT ENGINE: turns hypotheses into TESTED_EFFECTs (or rejects them).

Design: within-person day-level A/B. On creation, the experiment window's days are
randomly assigned to treatment/control (balanced, seeded) to reduce confounding by
weekday or mood. The user follows the schedule; the engine compares the registered
daily metric between treatment and control days.
"""
from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import DeepWork, Energy, Experiment, LearningSession, Question, Sleep
from .insights import Insight
from .stats import mean, welch


def _daily(rows, value: Callable, agg=mean) -> dict[date, float]:
    acc = defaultdict(list)
    for r in rows:
        v = value(r)
        if v is not None:
            acc[r.day].append(v)
    return {d: agg(v) for d, v in acc.items()}


METRICS: dict[str, tuple[str, Callable[[Session, date, date], dict[date, float]]]] = {
    "retention": ("avg delayed-recall retention (0-100)", lambda db, a, b: _daily(
        db.query(LearningSession).filter(LearningSession.day.between(a, b)).all(), lambda r: r.retention_score)),
    "focus": ("avg session focus (1-5)", lambda db, a, b: _daily(
        db.query(LearningSession).filter(LearningSession.day.between(a, b)).all(), lambda r: r.focus)),
    "law_accuracy": ("law question accuracy %", lambda db, a, b: _daily(
        db.query(Question).filter(Question.domain == "law", Question.day.between(a, b)).all(),
        lambda r: 100.0 if r.correct else 0.0)),
    "german_accuracy": ("German question accuracy %", lambda db, a, b: _daily(
        db.query(Question).filter(Question.domain == "german", Question.day.between(a, b)).all(),
        lambda r: 100.0 if r.correct else 0.0)),
    "deep_work_min": ("deep work minutes / day", lambda db, a, b: _daily(
        db.query(DeepWork).filter(DeepWork.day.between(a, b)).all(), lambda r: r.minutes, agg=sum)),
    "sleep_hours": ("sleep hours", lambda db, a, b: _daily(
        db.query(Sleep).filter(Sleep.day.between(a, b)).all(), lambda r: r.duration_min / 60)),
    "energy": ("energy (1-10)", lambda db, a, b: _daily(
        db.query(Energy).filter(Energy.day.between(a, b)).all(), lambda r: r.level)),
}


def schedule(start: date, days: int, seed: int | None = None) -> list[str]:
    """Balanced random assignment of treatment days."""
    all_days = [start + timedelta(days=i) for i in range(days)]
    rng = random.Random(seed if seed is not None else start.toordinal())
    picked = rng.sample(all_days, days // 2)
    return sorted(d.isoformat() for d in picked)


def create(db: Session, hypothesis: str, domain: str, intervention: str, metric: str, start: date,
           days: int = 14, seed: int | None = None) -> Experiment:
    if metric not in METRICS:
        raise ValueError(f"unknown metric '{metric}'. Options: {', '.join(METRICS)}")
    if days < 6:
        raise ValueError("experiments need at least 6 days")
    exp = Experiment(hypothesis=hypothesis, domain=domain, intervention=intervention, metric=metric,
                     start_day=start, days=days, treatment_days=schedule(start, days, seed))
    db.add(exp)
    db.flush()
    return exp


def is_treatment_day(exp: Experiment, day: date) -> bool:
    return day.isoformat() in (exp.treatment_days or [])


def evaluate(db: Session, exp: Experiment, today: date) -> dict:
    end = exp.start_day + timedelta(days=exp.days - 1)
    series = METRICS[exp.metric][1](db, exp.start_day, min(end, today))
    tset = set(exp.treatment_days or [])
    treat = [v for d, v in series.items() if d.isoformat() in tset]
    ctrl = [v for d, v in series.items() if d.isoformat() not in tset]
    finished = today >= end
    w = welch(treat, ctrl)
    if not w:
        result = {"status": "insufficient", "n_treatment": len(treat), "n_control": len(ctrl),
                  "message": "Need ≥3 measured days in each arm.", "finished": finished}
        if finished:
            exp.status, exp.decision = "evaluated", "modify"
            result["decision"] = "modify"
        exp.result = result
        return result
    base = w["mean_b"] or 1e-9
    rel = 100 * w["diff"] / abs(base)
    n = len(treat) + len(ctrl)
    conf = "high" if w["p"] < 0.01 and min(len(treat), len(ctrl)) >= 7 else "medium" if w["p"] < 0.05 else "low"
    if not finished:
        decision = ""
    elif conf in ("medium", "high"):
        decision = "continue" if w["diff"] > 0 else "reject"
    else:
        decision = "modify" if w["diff"] > 0 else "reject"
    ins = Insight(
        statement=f"{exp.intervention}: {METRICS[exp.metric][0]} {rel:+.0f}% "
                  f"({w['mean_a']:.1f} vs {w['mean_b']:.1f})",
        epistemic="TESTED_EFFECT" if finished else "HYPOTHESIS",
        confidence=conf, n=n, data_used=[exp.metric],
        stats={"p": round(w["p"], 4), "d": round(w["d"], 2), "diff": round(w["diff"], 2)},
        caveat="Randomized within-person day assignment; unblinded and short — re-test before big changes."
               if finished else "Experiment still running; interim look only.",
    )
    result = {"status": "final" if finished else "interim", "effect_pct": round(rel, 1), "confidence": conf,
              "n_treatment": len(treat), "n_control": len(ctrl), "decision": decision,
              "insight": ins.to_dict(), "finished": finished}
    exp.result = result
    if finished:
        exp.status, exp.decision = "evaluated", decision
    return result
