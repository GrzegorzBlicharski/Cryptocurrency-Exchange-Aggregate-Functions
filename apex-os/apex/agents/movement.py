"""Fitness & Movement Agent: regular, safe, sustainable movement. Not a medical coach."""
from __future__ import annotations

from datetime import timedelta

from ..engines.insights import observation
from ..engines.stats import mean
from ..models import Movement, Workout
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient

WEEKLY_ACTIVE_GUIDELINE = 150  # general public-health guideline for moderate activity (min/week)


def acwr(workouts: list[Workout], day) -> float | None:
    """Acute:chronic workload ratio (7d load / avg weekly load over 28d)."""
    acute = sum(w.load for w in workouts if w.day > day - timedelta(days=7))
    chronic = sum(w.load for w in workouts if w.day > day - timedelta(days=28)) / 4
    if chronic <= 0:
        return None
    return acute / chronic


class MovementAgent(Agent):
    name = "movement"
    domain = "fitness"
    label = "Movement"
    scopes = frozenset({"movement", "workouts"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        mv7 = ctx.rows(Movement, days=7)
        wk28 = ctx.rows(Workout, days=28)
        if not mv7 and not wk28:
            return AgentReport(self.name, insufficient(self.domain, self.label, "Log daily steps or a workout."))

        steps = mean([m.steps for m in mv7])
        target = ctx.settings.steps_target
        wk7 = [w for w in wk28 if w.day > ctx.day - timedelta(days=7)]
        active_week = sum(m.active_min for m in mv7) + sum(w.minutes for w in wk7 if w.kind != "walk")
        last_workout = max((w.day for w in wk28), default=None)
        days_since = (ctx.day - last_workout).days if last_workout else None
        ratio = acwr(wk28, ctx.day)
        sedentary = mean([m.sedentary_min for m in mv7 if m.sedentary_min])

        parts = []
        if steps is not None:
            parts.append((min(steps / target, 1) * 100, 0.4))
        parts.append((min(active_week / WEEKLY_ACTIVE_GUIDELINE, 1) * 100, 0.4))
        freq = len({w.day for w in wk7})
        parts.append((min(freq / 3, 1) * 100, 0.2))
        score = round(sum(v * w for v, w in parts) / sum(w for _, w in parts), 1)

        signals, cands = [], []
        data = ["movement (7d)", "workouts (28d)"]
        if steps is not None and len(mv7) >= 3 and steps < 0.6 * target:
            signals.append(Signal(
                kind="PROBLEM", severity=3, key="movement:low-steps", inbox_kind="WARNING",
                title=f"Low movement: {steps:,.0f} steps/day (target {target:,})",
                so_what="Long sedentary stretches cost energy and focus; a walk is the cheapest fix.",
                insight=observation(f"7-day steps avg {steps:,.0f}", len(mv7), data[:1]),
            ))
        if steps is None or steps < target:
            cands.append(CandidateAction(
                key="movement:walk", agent=self.name, domain=self.domain,
                title="30-min brisk walk (ideally outside, daylight)",
                detail="Can double as listening practice (German podcast) — but keep the phone otherwise away.",
                minutes=30, impact=3, urgency=2, energy_cost=1, cognitive=False, protected=True, confidence=0.75,
                why=Why(data_used=data[:1], reasoning=f"Steps {steps or 0:,.0f}/day vs target {target:,}.",
                        expected_benefit="More energy, better sleep pressure, a cognitive break.", confidence="medium",
                        alternatives=["Two 15-min walks", "Cycling errands"]),
            ))
        if ratio is not None and ratio > 1.5:
            signals.append(Signal(
                kind="ANOMALY", severity=4, key="movement:load-spike", inbox_kind="WARNING",
                title=f"Training load spike (acute:chronic {ratio:.2f})",
                so_what="Sudden load jumps raise injury and overreaching risk. Keep today easy.",
                insight=observation(f"ACWR {ratio:.2f}", len(wk28), data[1:]),
            ))
        elif days_since is None or days_since >= 3:
            cands.append(CandidateAction(
                key="movement:workout", agent=self.name, domain=self.domain,
                title="Training session (strength or cardio, moderate)",
                detail="45 min at a conversational/controlled effort (RPE 5-7). Progress gradually.",
                minutes=45, impact=3, urgency=2 if days_since is None or days_since < 5 else 3, energy_cost=3,
                cognitive=False, confidence=0.7,
                why=Why(data_used=data[1:], reasoning=f"Days since last workout: {days_since if days_since is not None else 'none in 28 d'}.",
                        expected_benefit="Keeps regularity; supports energy and sleep.", confidence="medium",
                        downside="Skip if recovery is flagged low today."),
            ))
        if sedentary and sedentary > 600:
            cands.append(CandidateAction(
                key="movement:mobility", agent=self.name, domain=self.domain,
                title="10-min mobility break between study blocks",
                detail="Hips, thoracic spine, shoulders. Set it as the break between two focus blocks.",
                minutes=10, impact=2, urgency=2, energy_cost=1, cognitive=False, confidence=0.6,
                why=Why(data_used=data[:1], reasoning=f"~{sedentary / 60:.1f} h sedentary per day.",
                        expected_benefit="Interrupts long sitting at near-zero cost.", confidence="low"),
            ))

        status = DomainStatus(
            self.domain, self.label, score,
            f"{steps or 0:,.0f} steps/day · {active_week} active min/wk · {freq} sessions/wk",
            ("Regular and sustainable." if score >= 75 else
             "Below a healthy baseline — add low-cost movement before adding intensity."),
            "flat",
        )
        status.metrics = {"steps_7d": steps, "active_min_week": active_week, "days_since_workout": days_since,
                          "acwr": round(ratio, 2) if ratio else None, "sessions_week": freq}
        return AgentReport(self.name, status, signals, cands)
