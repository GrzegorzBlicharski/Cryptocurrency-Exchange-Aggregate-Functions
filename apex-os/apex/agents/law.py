"""Law Development Agent: LAW KNOWLEDGE GRAPH + practical-skill coverage."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..engines import velocity
from ..engines.insights import observation
from ..models import Error, LearningSession, Question, Test
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient

PRACTICAL = ("drafting", "contract_analysis", "argumentation", "research", "case_analysis")
RETENTION_GAP_DAYS = 7


def classify(acc: float | None, n: int) -> str:
    if acc is None or n == 0:
        return "UNMEASURED"
    if acc >= 80:
        return "STRONG"
    if acc >= 65:
        return "DEVELOPING"
    if acc >= 50:
        return "WEAK"
    return "CRITICAL GAP"


class LawAgent(Agent):
    name = "law"
    domain = "law"
    label = "Law"
    scopes = frozenset({"learning_sessions", "law_sessions", "tests", "questions", "errors"})

    def knowledge_graph(self, ctx: AgentContext) -> dict:
        qs = ctx.rows(Question, days=120, domain="law")
        tests = ctx.rows(Test, days=120, domain="law")
        sessions = ctx.rows(LearningSession, days=120, domain="law")

        areas: dict[str, dict] = defaultdict(lambda: {"q": [], "tests": [], "topics": defaultdict(list),
                                                      "minutes": 0, "last": None, "times": []})
        for q in qs:
            a = areas[q.area]
            a["q"].append(q)
            a["topics"][q.topic or "general"].append(q.correct)
            if q.time_sec:
                a["times"].append(q.time_sec)
        for t in tests:
            areas[t.area or "general"]["tests"].append(t)
        practice_days: dict[str, list] = defaultdict(list)
        for s in sessions:
            if s.law:
                a = areas[s.law.area]
                a["minutes"] += s.minutes
                practice_days[s.law.area].append(s.day)
        for area, a in areas.items():
            days = practice_days[area] + [q.day for q in a["q"]] + [t.day for t in a["tests"]]
            a["last"] = max(days) if days else None

        graph = {}
        for area, a in sorted(areas.items()):
            recent_q = [q for q in a["q"] if q.day > ctx.day - timedelta(days=60)]
            correct = [1.0 if q.correct else 0.0 for q in recent_q]
            n = len(correct)
            acc = 100 * sum(correct) / n if n else None
            # Blend in test results (each test counts as 10 questions of evidence).
            if a["tests"]:
                tpct = [t.pct for t in a["tests"][-3:]]
                tavg = sum(tpct) / len(tpct)
                weight_t = 10 * len(tpct)
                acc = tavg if acc is None else (acc * n + tavg * weight_t) / (n + weight_t)
                n += weight_t
            # Retention: accuracy on questions answered after a >= 7-day gap in that area.
            qdays = sorted({q.day for q in a["q"]})
            gap_days = {d for prev, d in zip(qdays, qdays[1:], strict=False) if (d - prev).days >= RETENTION_GAP_DAYS}
            after_gap = [1.0 if q.correct else 0.0 for q in a["q"] if q.day in gap_days]
            retention = round(100 * sum(after_gap) / len(after_gap), 1) if len(after_gap) >= 5 else None
            topics = sorted(
                ({"topic": t, "accuracy": round(100 * sum(v) / len(v), 1), "n": len(v),
                  "status": classify(100 * sum(v) / len(v), len(v))} for t, v in a["topics"].items()),
                key=lambda x: x["accuracy"],
            )
            graph[area] = {
                "status": classify(acc, n),
                "accuracy": round(acc, 1) if acc is not None else None,
                "evidence_n": n,
                "confidence": "high" if n >= 40 else "medium" if n >= 15 else "low",
                "retention_after_gap": retention,
                "minutes_120d": a["minutes"],
                "days_since_practice": (ctx.day - a["last"]).days if a["last"] else None,
                "avg_time_sec": round(sum(a["times"]) / len(a["times"]), 1) if a["times"] else None,
                "topics": topics,
            }
        return graph

    def assess(self, ctx: AgentContext) -> AgentReport:
        graph = self.knowledge_graph(ctx)
        sessions = ctx.rows(LearningSession, days=30, domain="law")
        if not graph and not sessions:
            return AgentReport(self.name, insufficient(
                self.domain, self.label, "Log law questions (area + correct/incorrect) or a law session."))

        measured = {a: g for a, g in graph.items() if g["accuracy"] is not None}
        score = None
        if measured:
            tot = sum(g["evidence_n"] for g in measured.values())
            score = round(sum(g["accuracy"] * g["evidence_n"] for g in measured.values()) / tot, 1)

        practical_done = {s.law.activity for s in sessions if s.law} & set(PRACTICAL)
        practical_missing = [p for p in PRACTICAL if p not in practical_done]

        # Velocity from daily question accuracy (days with >=5 questions) + tests.
        by_day = defaultdict(list)
        for q in ctx.rows(Question, days=120, domain="law"):
            by_day[q.day].append(1.0 if q.correct else 0.0)
        meas = [(d, 100 * sum(v) / len(v)) for d, v in by_day.items() if len(v) >= 5]
        meas += [(t.day, t.pct) for t in ctx.rows(Test, days=120, domain="law")]
        effort = [(s.day, s.minutes) for s in ctx.rows(LearningSession, days=120, domain="law")]
        vel = velocity.compute(meas, effort, ctx.day)

        signals, cands = [], []
        data = ["questions (120d)", "tests (120d)", "law_sessions"]
        critical = sorted([a for a, g in graph.items() if g["status"] in ("CRITICAL GAP", "WEAK")],
                          key=lambda a: graph[a]["accuracy"] or 0)
        for area in critical[:2]:
            g = graph[area]
            worst_topic = g["topics"][0]["topic"] if g["topics"] else area
            sev = 4 if g["status"] == "CRITICAL GAP" and g["confidence"] != "low" else 3
            signals.append(Signal(
                kind="SKILL_GAP", severity=sev, key=f"law:gap:{area}", inbox_kind="WARNING",
                title=f"Law · {area}: {g['status']} ({g['accuracy']:.0f}%)",
                so_what=f"Weakest topic: {worst_topic}. This area drags your overall law accuracy most.",
                insight=observation(f"{area} accuracy {g['accuracy']:.0f}% over {g['evidence_n']} evidence points",
                                    g["evidence_n"], data),
            ))
            cand = CandidateAction(
                key=f"law:gap:{area}", agent=self.name, domain=self.domain,
                title=f"Law {area}: close gap on '{worst_topic}'",
                detail="Re-read the rule (15 min), then 20 exam-style questions; log every wrong answer as an error.",
                minutes=50, impact=4 if g["status"] == "CRITICAL GAP" else 3, urgency=2, energy_cost=4,
                confidence=0.65,
                why=Why(data_used=data,
                        reasoning=f"{area} is {g['status']} at {g['accuracy']:.0f}% (n={g['evidence_n']}, "
                                  f"{g['confidence']} confidence). Gaps cost more points than polishing strong areas.",
                        expected_benefit="Largest accuracy gain per hour in law.",
                        confidence=g["confidence"],
                        alternatives=[f"Spaced review of {a}" for a in list(measured)[:2] if a != area],
                        downside="High cognitive load; schedule in your best focus window."),
            )
            cand.smaller = CandidateAction(
                key=cand.key + ":min", agent=self.name, domain=self.domain,
                title=f"Law {area}: 10 targeted questions", detail="10 questions on the weakest topic + review errors.",
                minutes=20, impact=3, urgency=2, energy_cost=3, confidence=0.6, why=cand.why)
            cands.append(cand)

        stale = [a for a, g in graph.items()
                 if g["status"] in ("STRONG", "DEVELOPING") and (g["days_since_practice"] or 0) >= 21]
        if stale:
            a = max(stale, key=lambda x: graph[x]["days_since_practice"])
            cands.append(CandidateAction(
                key=f"law:spaced:{a}", agent=self.name, domain=self.domain,
                title=f"Spaced review: {a} ({graph[a]['days_since_practice']} days untouched)",
                detail="15 mixed questions from memory, no notes first.",
                minutes=25, impact=2, urgency=2, energy_cost=3, confidence=0.6,
                why=Why(data_used=data, reasoning="Untouched areas decay; a short retrieval session protects retention.",
                        expected_benefit="Keeps a strong area strong at low cost.", confidence="medium"),
            ))

        if practical_missing and sessions:
            p = practical_missing[0]
            cands.append(CandidateAction(
                key=f"law:practical:{p}", agent=self.name, domain=self.domain,
                title=f"Practical skill: {p.replace('_', ' ')}",
                detail="One realistic exercise (e.g. draft a clause / analyse a short contract / brief a case).",
                minutes=45, impact=3, urgency=1, energy_cost=4, confidence=0.55,
                why=Why(data_used=["law_sessions (30d)"],
                        reasoning=f"No {p.replace('_', ' ')} practice in 30 days. Knowledge without drafting/analysis "
                                  "practice transfers poorly to real legal work.",
                        expected_benefit="Builds employable practical capability, not just recall.",
                        confidence="low", downside="Hard to self-grade; consider a model answer."),
            ))

        open_errors = ctx.query(Error).filter_by(domain="law", status="open").count()
        if score is None:
            status = DomainStatus(self.domain, self.label, None, "Sessions logged, no question/test results",
                                  "Log question results so APEX can map strong vs weak areas.", "unknown",
                                  needs="question or test results")
        else:
            counts = defaultdict(int)
            for g in graph.values():
                counts[g["status"]] += 1
            worst = critical[0] if critical else None
            status = DomainStatus(
                self.domain, self.label, score,
                f"Accuracy {score:.0f}% · {counts['STRONG']} strong / {counts['WEAK'] + counts['CRITICAL GAP']} weak areas",
                (f"Biggest gap: {worst}. " if worst else "No weak areas measured. ")
                + (f"Velocity {vel.units_per_100h:+.1f}/100h." if vel.known else f"Velocity: {vel.reason}."),
                "unknown",
            )
        status.metrics = {"practical_missing": practical_missing, "open_errors": open_errors,
                          "velocity": vel.__dict__, "areas": len(graph)}
        return AgentReport(self.name, status, signals, cands, extras={"knowledge_graph": graph})
