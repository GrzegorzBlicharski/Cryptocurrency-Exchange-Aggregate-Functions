"""Health & Recovery Agent.

SAFETY: never diagnoses, never decides about medication or treatment.
Pattern: DETECT -> FLAG -> EXPLAIN -> RECOMMEND APPROPRIATE NEXT STEP.
"""
from __future__ import annotations

from datetime import timedelta

from ..engines.insights import observation
from ..engines.stats import mean, std
from ..engines.sustainability import sleep_score as sus_sleep_score
from ..models import Energy, Recovery, Sleep
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient

MEDICAL_NOTE = ("APEX is not a medical tool and cannot diagnose. If this persists or you feel unwell, "
                "consider discussing it with a doctor.")


def _clock_to_min(hhmm: str) -> int | None:
    """Minutes after 18:00 so that 23:30 and 00:30 are close together."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except (ValueError, AttributeError):
        return None
    return ((h - 18) % 24) * 60 + m


class RecoveryAgent(Agent):
    name = "recovery"
    domain = "recovery"
    label = "Recovery"
    scopes = frozenset({"sleep", "energy", "recovery"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        target_min = ctx.settings.sleep_target_hours * 60
        sleep14 = ctx.rows(Sleep, days=14)
        sleep7 = [s for s in sleep14 if s.day > ctx.day - timedelta(days=7)]
        energy7 = ctx.rows(Energy, days=7)
        energy28 = ctx.rows(Energy, days=28)
        rec28 = ctx.rows(Recovery, days=28)
        rec3 = [r for r in rec28 if r.day > ctx.day - timedelta(days=3)]

        if not sleep14 and not energy7 and not rec28:
            return AgentReport(self.name, insufficient(self.domain, self.label,
                                                       "Log sleep (duration) and a daily energy rating."))

        avg_sleep = mean([s.duration_min for s in sleep7])
        debt = sum(max(0.0, target_min - s.duration_min) for s in sleep7)
        bed_std = std([m for m in (_clock_to_min(s.bed_time) for s in sleep14) if m is not None])
        en7 = mean([e.level for e in energy7])
        en28 = mean([e.level for e in energy28])
        subj = mean([r.subjective for r in rec3])

        parts = []
        if avg_sleep is not None:
            sleep_score = sus_sleep_score(avg_sleep / target_min)
            if bed_std is not None and bed_std > 60:
                sleep_score -= min(20, (bed_std - 60) / 3)
            parts.append((sleep_score, 0.5))
        if en7 is not None:
            parts.append((en7 * 10, 0.25))
        if subj is not None:
            parts.append((subj * 10, 0.25))
        score = round(sum(v * w for v, w in parts) / sum(w for _, w in parts), 1) if parts else None

        signals, cands = [], []
        data = ["sleep (14d)", "energy (28d)", "recovery (28d)"]
        if avg_sleep is not None and len(sleep7) >= 3 and avg_sleep < target_min - 30:
            signals.append(Signal(
                kind="PROBLEM", severity=4 if avg_sleep < target_min - 75 else 3, key="recovery:sleep-short",
                inbox_kind="WARNING",
                title=f"Sleep avg {avg_sleep / 60:.1f} h vs target {target_min / 60:.1f} h (debt {debt / 60:.1f} h / 7 d)",
                so_what="Short sleep reduces focus and retention; APEX lowers today's cognitive load.",
                insight=observation(f"7-day sleep avg {avg_sleep / 60:.2f} h", len(sleep7), data[:1]),
            ))
            cands.append(CandidateAction(
                key="recovery:sleep-window", agent=self.name, domain=self.domain,
                title=f"Protect sleep: lights out by target, {int(target_min // 60)}h+ window",
                detail="Screens off 45 min before bed; no caffeine after 14:00; same wake time tomorrow.",
                minutes=0, impact=4, urgency=3, energy_cost=1, cognitive=False, protected=True, confidence=0.7,
                why=Why(data_used=data[:1], reasoning=f"Sleep debt {debt / 60:.1f} h over 7 days.",
                        expected_benefit="Restores tomorrow's focus capacity; cheapest performance lever available.",
                        confidence="medium", downside="Evening time is shorter."),
            ))
        if bed_std is not None and bed_std > 75 and len(sleep14) >= 7:
            signals.append(Signal(
                kind="ANOMALY", severity=2, key="recovery:irregular", inbox_kind="INSIGHT",
                title=f"Irregular bedtime (±{bed_std:.0f} min)",
                so_what="Irregular timing can lower sleep quality even at equal duration.",
                insight=observation(f"bedtime std {bed_std:.0f} min", len(sleep14), data[:1]),
            ))
        if en7 is not None and en28 is not None and len(energy28) >= 10 and en7 < en28 - 1.0:
            signals.append(Signal(
                kind="TREND", severity=3, key="recovery:energy-drop", inbox_kind="WARNING",
                title=f"Energy down: 7-day {en7:.1f} vs 28-day {en28:.1f}",
                so_what="A sustained energy drop is an early overload signal; plan is reduced.",
                insight=observation(f"energy 7d {en7:.1f} vs 28d {en28:.1f}", len(energy28), data[1:2]),
            ))

        # Resting HR flag (only if the user provides it). Detect -> flag -> explain -> next step.
        hr = [(r.day, r.resting_hr) for r in rec28 if r.resting_hr]
        if len(hr) >= 10:
            base = mean([v for d, v in hr if d <= ctx.day - timedelta(days=7)])
            last = mean([v for d, v in hr if d > ctx.day - timedelta(days=7)])
            if base and last and last - base >= 7:
                signals.append(Signal(
                    kind="ANOMALY", severity=4, key="recovery:rhr-elevated", inbox_kind="WARNING",
                    title=f"Resting HR {last:.0f} vs baseline {base:.0f} bpm",
                    so_what="Often seen with poor recovery, stress or illness. Reduce training intensity today. "
                            + MEDICAL_NOTE,
                    insight=observation(f"RHR +{last - base:.0f} bpm vs baseline", len(hr), ["recovery"]),
                ))
                cands.append(CandidateAction(
                    key="recovery:easy-day", agent=self.name, domain=self.domain,
                    title="Easy day: no hard training, light walk only",
                    detail="Swap intense training for an easy walk; prioritize sleep. " + MEDICAL_NOTE,
                    minutes=20, impact=3, urgency=3, energy_cost=1, cognitive=False, protected=True, confidence=0.6,
                    why=Why(data_used=["recovery"], reasoning="Elevated resting HR vs your own baseline.",
                            expected_benefit="Avoids digging a deeper recovery hole.", confidence="low",
                            downside="Training stimulus postponed."),
                ))
        if subj is not None and subj <= 4:
            cands.append(CandidateAction(
                key="recovery:rest-block", agent=self.name, domain=self.domain,
                title="Scheduled rest block (30 min, offline)",
                detail="Walk outside, no phone, or lie down. Not a reward — part of the plan.",
                minutes=30, impact=3, urgency=3, energy_cost=1, cognitive=False, protected=True, confidence=0.6,
                why=Why(data_used=["recovery (3d)"], reasoning=f"Subjective recovery {subj:.1f}/10 over 3 days.",
                        expected_benefit="Raises tomorrow's capacity.", confidence="low"),
            ))

        headline = " · ".join(x for x in [
            f"Sleep {avg_sleep / 60:.1f} h" if avg_sleep is not None else "",
            f"Energy {en7:.1f}/10" if en7 is not None else "",
            f"Recovered {subj:.0f}/10" if subj is not None else "",
        ] if x)
        so_what = ("Recovery limits today's plan." if score is not None and score < 65
                   else "Recovery supports a normal plan." if score is not None else "Partial data.")
        status = DomainStatus(self.domain, self.label, score, headline or "Partial data", so_what,
                              "down" if en7 and en28 and en7 < en28 - 0.5 else "flat")
        status.metrics = {"avg_sleep_min": avg_sleep, "sleep_debt_min_7d": debt, "bedtime_std_min": bed_std,
                          "energy_7d": en7, "energy_28d": en28, "subjective_3d": subj,
                          "sleep_ratio": (avg_sleep / target_min) if avg_sleep else None}
        return AgentReport(self.name, status, signals, cands)
