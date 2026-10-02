"""German Intelligence Agent: is the user actually getting better, skill by skill?"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..engines import velocity
from ..engines.insights import observation
from ..engines.stats import ewma, slope_per_day
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient
from ..models import Error, LearningSession, Question, Test

SKILLS = ("speaking", "writing", "reading", "listening", "vocabulary", "grammar")
ACTIVE_TARGET = 0.5  # at least half of study time should be active production/recall

DRILLS = {
    "speaking": "Shadowing + 3 recorded 2-minute monologues on today's topic; replay and note errors",
    "writing": "Write 150 words, then self-correct against your top error list",
    "reading": "Read one authentic article; summarize it aloud in German",
    "listening": "Podcast segment with transcript: listen, dictate 5 sentences, compare",
    "vocabulary": "Spaced-repetition review + use 10 new words in your own sentences",
    "grammar": "Targeted drill on your most frequent grammar error + 10 production sentences",
}


class GermanAgent(Agent):
    name = "german"
    domain = "german"
    label = "German"
    scopes = frozenset({"learning_sessions", "language_sessions", "tests", "questions", "errors"})

    def _measurements(self, ctx: AgentContext) -> dict[str, list]:
        per_skill: dict[str, list] = defaultdict(list)
        for t in ctx.rows(Test, days=120, domain="german"):
            per_skill[t.area or "overall"].append((t.day, t.pct, "test"))
        by_day_area: dict = defaultdict(list)
        for q in ctx.rows(Question, days=120, domain="german"):
            by_day_area[(q.day, q.area)].append(1.0 if q.correct else 0.0)
        for (d, area), xs in by_day_area.items():
            if len(xs) >= 5:
                per_skill[area].append((d, 100 * sum(xs) / len(xs), "questions"))
        for s in ctx.rows(LearningSession, days=120, domain="german"):
            ls = s.language
            if ls and ls.accuracy is not None:
                per_skill[ls.skill].append((s.day, ls.accuracy, "session"))
        for k in per_skill:
            per_skill[k].sort(key=lambda x: x[0])
        return per_skill

    def assess(self, ctx: AgentContext) -> AgentReport:
        sessions = ctx.rows(LearningSession, days=28, domain="german")
        per_skill = self._measurements(ctx)
        recent = [s for s in sessions if s.day > ctx.day - timedelta(days=14)]

        minutes_by_skill = defaultdict(int)
        active_min = passive_min = 0
        for s in sessions:
            ls = s.language
            skill = ls.skill if ls else "other"
            minutes_by_skill[skill] += s.minutes
            if ls and ls.mode == "passive":
                passive_min += s.minutes
            else:
                active_min += s.minutes
        total_min = active_min + passive_min

        competency = {}
        trend = {}
        for skill in SKILLS:
            pts = per_skill.get(skill, [])
            if pts:
                # Tests weigh more than in-session accuracy (self-graded, easier).
                weighted = [v for _, v, src in pts for _ in range(2 if src != "session" else 1)]
                competency[skill] = round(ewma(weighted[-12:]), 1)
                sl = slope_per_day([(d, v) for d, v, _ in pts[-10:]])
                trend[skill] = sl

        if not sessions and not competency:
            return AgentReport(self.name, insufficient(
                self.domain, self.label, "Log a German session or a placement test to start."))

        all_meas = sorted((d, v) for pts in per_skill.values() for d, v, src in pts if src != "session")
        effort = [(s.day, s.minutes) for s in ctx.rows(LearningSession, days=120, domain="german")]
        vel = velocity.compute(all_meas, effort, ctx.day)

        score = round(sum(competency.values()) / len(competency), 1) if competency else None
        weakest = min(competency, key=competency.get) if competency else None
        unmeasured = [s for s in SKILLS if s not in competency]
        neglected = [s for s in SKILLS if not any(x.language and x.language.skill == s for x in recent)]
        active_ratio = active_min / total_min if total_min else None

        signals: list[Signal] = []
        cands: list[CandidateAction] = []
        data = ["learning_sessions (28d)", "language_sessions", "tests (120d)", "questions (120d)"]

        if active_ratio is not None and total_min >= 120 and active_ratio < ACTIVE_TARGET:
            signals.append(Signal(
                kind="PROBLEM", severity=3, key="german:passive-heavy", inbox_kind="WARNING",
                title=f"German study is {100 * (1 - active_ratio):.0f}% passive",
                so_what="Passive input alone converts slowly into speaking/writing ability. Shift time to active recall/production.",
                insight=observation(f"Active share {active_ratio:.0%} of {total_min} min in 28 days", len(sessions), data[:2]),
            ))
        declining = [k for k, v in trend.items() if v is not None and v < -0.15]
        for skill in declining:
            signals.append(Signal(
                kind="TREND", severity=3, key=f"german:decline:{skill}", inbox_kind="WARNING",
                title=f"German {skill} trending down ({trend[skill] * 7:+.1f} pts/week)",
                so_what=f"Retention of {skill} is slipping; schedule a spaced review before it compounds.",
                insight=observation(f"{skill} slope {trend[skill] * 7:+.1f} pts/week over last measurements",
                                    len(per_skill[skill]), data[2:]),
            ))
        if vel.known and vel.hours >= 15 and vel.units_per_100h is not None and vel.units_per_100h <= 0:
            signals.append(Signal(
                kind="PROBLEM", severity=4, key="german:hours-not-converting", inbox_kind="WARNING",
                title=f"{vel.hours:.0f} h of German without measured improvement",
                so_what="Effort is not converting into competency. Change method, not volume.",
                insight=observation(f"velocity {vel.units_per_100h:+.1f} units/100h", vel.measurements, data),
            ))

        if weakest:
            gap = competency[weakest]
            cand = CandidateAction(
                key=f"german:weakest:{weakest}", agent=self.name, domain=self.domain,
                title=f"German {weakest}: targeted active block",
                detail=DRILLS[weakest], minutes=45, impact=4, urgency=2, energy_cost=3,
                confidence=0.65,
                why=Why(
                    data_used=data,
                    reasoning=f"{weakest} is your lowest measured skill ({gap:.0f}/100). The weakest skill caps "
                              f"overall usable German; active practice there has the highest marginal return.",
                    expected_benefit="Fastest gain in overall competency per hour.",
                    confidence="medium" if len(per_skill.get(weakest, [])) >= 3 else "low",
                    alternatives=[f"Work on {s}" for s in sorted(competency, key=competency.get)[1:3]],
                    downside="Feels harder than passive input; expect lower subjective enjoyment.",
                ),
            )
            cand.smaller = CandidateAction(
                key=cand.key + ":min", agent=self.name, domain=self.domain,
                title=f"German {weakest}: 20-min minimum dose", detail=DRILLS[weakest], minutes=20,
                impact=3, urgency=2, energy_cost=2, confidence=0.6, why=cand.why,
            )
            cands.append(cand)

        open_errors = sorted(
            [e for e in ctx.query(Error).filter_by(domain="german", status="open").all() if e.occurrences >= 3],
            key=lambda e: -e.occurrences,
        )
        if open_errors:
            e = open_errors[0]
            cands.append(CandidateAction(
                key=f"german:error:{e.id}", agent=self.name, domain=self.domain,
                title=f"Fix recurring error: {e.category}",
                detail="15-minute drill: rule, 10 contrast examples, 10 own sentences, then retest tomorrow.",
                minutes=15, impact=3, urgency=2, energy_cost=2, confidence=0.6,
                why=Why(data_used=["errors"], reasoning=f"Seen {e.occurrences}x, last {e.last_seen}. "
                        "Repeated errors fossilize if not addressed.",
                        expected_benefit="Removes a systematic error from speaking/writing.",
                        confidence="medium", downside="Small scope; won't move overall score alone."),
            ))

        last_test = max((d for pts in per_skill.values() for d, _, src in pts if src == "test"), default=None)
        if last_test is None or (ctx.day - last_test).days > 21 or unmeasured:
            what = ", ".join(unmeasured) if unmeasured else "all skills"
            cands.append(CandidateAction(
                key="german:diagnostic", agent=self.name, domain=self.domain,
                title=f"German diagnostic test ({what})",
                detail="Short timed test per skill. Without measurement APEX can't verify progress.",
                minutes=30, impact=3, urgency=2 if last_test else 3, energy_cost=3, confidence=0.8,
                why=Why(data_used=["tests"], reasoning=(
                    f"Last test: {last_test or 'never'}. Unmeasured skills: {what}."),
                    expected_benefit="Turns hours into verifiable progress data; sharpens targeting.",
                    confidence="high", downside="30 min not spent practicing."),
            ))

        if score is None:
            status = DomainStatus(self.domain, self.label, None, f"{total_min} min studied in 28 d, no measurements",
                                  "Hours are logged but competency is unverified — take a diagnostic test.",
                                  "unknown", needs="test results or session accuracy")
        else:
            t = [v for v in trend.values() if v is not None]
            tr = "unknown" if not t else ("up" if sum(t) / len(t) > 0.05 else "down" if sum(t) / len(t) < -0.05 else "flat")
            so_what = (f"Weakest: {weakest} ({competency[weakest]:.0f}). "
                       + (f"Velocity {vel.units_per_100h:+.1f}/100h." if vel.known else f"Velocity: {vel.reason}."))
            status = DomainStatus(self.domain, self.label, score,
                                  f"Competency {score:.0f}/100 · {total_min / 60:.1f} h in 28 d", so_what, tr)
        status.metrics = {
            "competency": competency, "minutes_by_skill": dict(minutes_by_skill),
            "active_ratio": round(active_ratio, 2) if active_ratio is not None else None,
            "neglected_14d": neglected, "unmeasured": unmeasured, "weakest": weakest,
            "velocity": vel.__dict__,
        }
        return AgentReport(self.name, status, signals, cands)
