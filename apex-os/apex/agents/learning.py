"""Learning Intelligence Agent: HOW I LEARN BEST.

Compares outcomes (delayed-recall retention, else self-rated focus) across time of day,
session length, method and prior sleep. Findings are HYPOTHESES/CORRELATIONS with a
confidence and are written to the Digital Twin; strong ones become experiment
suggestions — only experiments can turn them into TESTED_EFFECT.
"""
from __future__ import annotations

from collections import defaultdict
from ..engines.insights import Insight, correlation_insight
from ..engines.stats import welch
from ..models import LearningSession, Movement, Sleep
from .base import Agent, AgentContext, AgentReport, DomainStatus

MIN_PER_BUCKET = 5
MIN_DIFF = 8.0  # outcome points (0..100) worth talking about


def _outcome(s: LearningSession) -> float | None:
    if s.retention_score is not None:
        return s.retention_score
    if s.focus is not None:
        return s.focus * 20.0
    return None


def _tod(h: int | None) -> str | None:
    if h is None:
        return None
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening"


def _length(m: int) -> str:
    return "<45 min" if m < 45 else "45-75 min" if m <= 75 else ">75 min"


def _compare(groups: dict[str, list[float]], dimension: str, metric: str, data: list[str]) -> Insight | None:
    valid = {k: v for k, v in groups.items() if len(v) >= MIN_PER_BUCKET}
    if len(valid) < 2:
        return None
    best = max(valid, key=lambda k: sum(valid[k]) / len(valid[k]))
    rest = [x for k, v in valid.items() if k != best for x in v]
    w = welch(valid[best], rest)
    if not w or w["diff"] < MIN_DIFF:
        return None
    n = len(valid[best]) + len(rest)
    conf = "medium" if (w["p"] < 0.01 and n >= 20) else "low"
    return Insight(
        statement=f"{best} sessions ({dimension}) show higher {metric}: {w['mean_a']:.0f} vs {w['mean_b']:.0f}",
        epistemic="HYPOTHESIS",
        confidence=conf,
        n=n,
        data_used=data,
        stats={"diff": round(w["diff"], 1), "p": round(w["p"], 4), "d": round(w["d"], 2), "best": best},
        caveat="Observational: other factors (topic difficulty, sleep) may explain it. Test with an experiment.",
    )


class LearningAgent(Agent):
    name = "learning"
    domain = "learning"
    label = "Learning"
    scopes = frozenset({"learning_sessions", "sleep", "movement"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        sessions = [s for s in ctx.rows(LearningSession, days=90) if _outcome(s) is not None]
        metric = "retention" if any(s.retention_score is not None for s in sessions) else "focus"
        data = ["learning_sessions (90d)"]
        twin: list[dict] = []
        suggestions: list[dict] = []

        if len(sessions) < 2 * MIN_PER_BUCKET:
            status = DomainStatus(self.domain, self.label, None, f"{len(sessions)} sessions with outcomes",
                                  f"Need ≥{2 * MIN_PER_BUCKET} sessions with focus/retention ratings to learn your "
                                  "patterns.", "unknown", needs="focus or retention ratings on sessions")
            return AgentReport(self.name, status, extras={"twin": twin, "experiment_suggestions": suggestions})

        by_tod, by_len, by_method = defaultdict(list), defaultdict(list), defaultdict(list)
        for s in sessions:
            o = _outcome(s)
            if (t := _tod(s.start_hour)):
                by_tod[t].append(o)
            by_len[_length(s.minutes)].append(o)
            if s.method:
                by_method[s.method].append(o)

        found = [
            ("WHEN I WORK BEST", _compare(by_tod, "time of day", metric, data)),
            ("HOW I LEARN BEST", _compare(by_len, "session length", metric, data)),
            ("HOW I LEARN BEST", _compare(by_method, "method", metric, data)),
        ]
        sleep = {s.day: s.duration_min for s in ctx.rows(Sleep, days=91)}
        steps = {m.day: m.steps for m in ctx.rows(Movement, days=91)}
        c_sleep = correlation_insight("prior-night sleep", metric, [sleep.get(s.day) for s in sessions],
                                      [_outcome(s) for s in sessions], data + ["sleep"])
        c_dist = correlation_insight("distractions", metric, [float(s.distractions) for s in sessions],
                                     [_outcome(s) for s in sessions], data)
        c_move = correlation_insight("same-day steps", metric, [steps.get(s.day) for s in sessions],
                                     [_outcome(s) for s in sessions], data + ["movement"])
        found += [("WHAT IMPROVES RETENTION", c_sleep), ("WHAT DESTROYS FOCUS", c_dist),
                  ("WHAT IMPROVES RETENTION", c_move)]

        for question, ins in found:
            if ins is None:
                continue
            twin.append({"question": question, **ins.to_dict()})
            if ins.epistemic == "HYPOTHESIS":
                suggestions.append({"hypothesis": ins.statement, "metric": metric,
                                    "intervention": f"Schedule sessions as: {ins.stats.get('best')}", "days": 14})

        # Sustainable volume: focus vs. daily study minutes.
        by_day = defaultdict(lambda: [0, []])
        for s in sessions:
            by_day[s.day][0] += s.minutes
            by_day[s.day][1].append(_outcome(s))
        heavy = [sum(v[1]) / len(v[1]) for v in by_day.values() if v[0] >= 240]
        light = [sum(v[1]) / len(v[1]) for v in by_day.values() if v[0] < 240]
        w = welch(heavy, light)
        if w and w["diff"] < -MIN_DIFF:
            twin.append({"question": "HOW MUCH WORK IS SUSTAINABLE",
                         **Insight(f"Days with ≥4 h study show lower {metric} ({w['mean_a']:.0f} vs {w['mean_b']:.0f})",
                                   "CORRELATION", "low", len(heavy) + len(light), data,
                                   {"diff": round(w["diff"], 1), "p": round(w["p"], 4)},
                                   "May reflect fatigue or harder material.").to_dict()})

        status = DomainStatus(self.domain, self.label, None,
                              f"{len(twin)} patterns from {len(sessions)} sessions",
                              (twin[0]["statement"] + f" ({twin[0]['epistemic'].lower()}, {twin[0]['confidence']} conf.)"
                               if twin else "No reliable pattern yet — keep rating focus/retention."),
                              "unknown")
        status.metrics = {"sessions": len(sessions), "metric": metric}
        return AgentReport(self.name, status, extras={"twin": twin, "experiment_suggestions": suggestions})
