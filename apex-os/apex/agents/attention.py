"""Attention & Digital Hygiene Agent: does less phone actually mean more deep work?"""
from __future__ import annotations

from datetime import timedelta

from ..engines.insights import correlation_insight, hypothesis, observation
from ..engines.stats import mean
from ..models import DeepWork, ScreenTime, Sleep
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient, stable_key

DEEP_WORK_TARGET = 120  # min/day
PHONE_LIMIT = 120  # min/day


class AttentionAgent(Agent):
    name = "attention"
    domain = "attention"
    label = "Attention"
    scopes = frozenset({"screen_time", "deep_work", "sleep"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        st60 = ctx.rows(ScreenTime, days=60)
        dw60 = ctx.rows(DeepWork, days=60)
        if not st60 and not dw60:
            return AgentReport(self.name, insufficient(self.domain, self.label,
                                                       "Log daily phone screen time and deep-work blocks."))
        st7 = [s for s in st60 if s.day > ctx.day - timedelta(days=7)]
        st_prev = [s for s in st60 if ctx.day - timedelta(days=14) < s.day <= ctx.day - timedelta(days=7)]
        dw7 = [d for d in dw60 if d.day > ctx.day - timedelta(days=7)]

        phone = mean([s.phone_min if s.phone_min is not None else s.total_min for s in st7])
        phone_prev = mean([s.phone_min if s.phone_min is not None else s.total_min for s in st_prev])
        social = mean([s.social_min for s in st7 if s.social_min is not None])
        dw_days = {}
        for d in dw60:
            dw_days[d.day] = dw_days.get(d.day, 0) + d.minutes
        days7 = [ctx.day - timedelta(days=i) for i in range(7)]
        dw_avg = sum(dw_days.get(d, 0) for d in days7) / 7 if dw60 else None
        dw_min7 = sum(d.minutes for d in dw7)
        interrupts_per_h = (sum(d.interruptions for d in dw7) / (dw_min7 / 60)) if dw_min7 else None
        switches = mean([d.context_switches for d in dw7])

        parts = []
        if dw_avg is not None:
            parts.append((min(dw_avg / DEEP_WORK_TARGET, 1) * 100, 0.6))
        if phone is not None:
            parts.append((100 if phone <= PHONE_LIMIT else max(0, 100 - (phone - PHONE_LIMIT) / 2), 0.4))
        score = round(sum(v * w for v, w in parts) / sum(w for _, w in parts), 1) if parts else None

        signals, cands, findings = [], [], []
        # Does phone use go with less deep work? Correlation only, never causation.
        phone_by_day = {s.day: (s.phone_min if s.phone_min is not None else s.total_min) for s in st60}
        common = sorted(set(phone_by_day) & set(dw_days))
        corr = correlation_insight("phone time", "deep work", [phone_by_day[d] for d in common],
                                   [dw_days[d] for d in common], ["screen_time (60d)", "deep_work (60d)"])
        if corr:
            findings.append(corr)
            if corr.stats["r"] < 0:
                findings.append(hypothesis(
                    "Reducing phone time increases deep work for you.", corr, corr.data_used))
        sleep_by_day = {s.day: s.duration_min for s in ctx.rows(Sleep, days=60)}
        # Social media on day D vs sleep in the night after (recorded on D+1).
        pairs = [(s.social_min, sleep_by_day.get(s.day + timedelta(days=1))) for s in st60 if s.social_min is not None]
        c2 = correlation_insight("social media time", "next-night sleep", [p[0] for p in pairs], [p[1] for p in pairs],
                                 ["screen_time (60d)", "sleep (60d)"])
        if c2:
            findings.append(c2)

        if phone is not None and phone_prev is not None and phone > phone_prev * 1.25 and phone > 90:
            signals.append(Signal(
                kind="TREND", severity=3, key="attention:phone-up", inbox_kind="WARNING",
                title=f"Phone time up {100 * (phone / phone_prev - 1):.0f}% week over week ({phone:.0f} min/day)",
                so_what="Rising phone time usually comes out of focus or sleep time.",
                insight=observation(f"phone 7d {phone:.0f} vs prior {phone_prev:.0f} min/day", len(st7) + len(st_prev),
                                    ["screen_time (14d)"]),
            ))
        for f in findings:
            signals.append(Signal(
                kind="TREND", severity=2, key=f"attention:finding:{stable_key(f.statement)}", inbox_kind="INSIGHT",
                title=f.statement, so_what=f.caveat, insight=f,
            ))

        cands.append(CandidateAction(
            key="attention:phone-free-block", agent=self.name, domain=self.domain,
            title="Phone in another room for your first deep-work block",
            detail="Physical distance beats willpower. Notifications off; one task only.",
            minutes=0, impact=3, urgency=2, energy_cost=1, cognitive=False, confidence=0.6,
            why=Why(data_used=["screen_time", "deep_work"],
                    reasoning=(f"Interruptions {interrupts_per_h:.1f}/h in deep work." if interrupts_per_h is not None
                               else "Low-cost default that protects focus."),
                    expected_benefit="More uninterrupted minutes in the blocks you already do.",
                    confidence=corr.confidence if corr else "low",
                    downside="Small: delayed replies.",
                    insight=corr.to_dict() if corr else None),
        ))
        if social is not None and social > 60:
            cands.append(CandidateAction(
                key="attention:social-cap", agent=self.name, domain=self.domain,
                title=f"Cap social media to 30 min today (avg {social:.0f})",
                detail="Use the OS app limit; batch it after your last study block.",
                minutes=0, impact=2, urgency=2, energy_cost=2, cognitive=False, confidence=0.5,
                why=Why(data_used=["screen_time"], reasoning=f"Social avg {social:.0f} min/day over 7 days.",
                        expected_benefit="Frees time and attention; may help sleep.", confidence="low"),
            ))

        headline = " · ".join(x for x in [
            f"Deep work {dw_avg:.0f} min/day" if dw_avg is not None else "",
            f"Phone {phone:.0f} min/day" if phone is not None else "",
        ] if x)
        status = DomainStatus(self.domain, self.label, score, headline,
                              (corr.statement if corr else
                               "Focus is protected." if score and score >= 75 else
                               "Focus time is below target; phone is the first lever to test."),
                              "up" if phone and phone_prev and phone < phone_prev * 0.9 else "flat")
        status.metrics = {"phone_7d": phone, "social_7d": social, "deep_work_7d_avg": dw_avg,
                          "interruptions_per_h": interrupts_per_h, "context_switches_avg": switches}
        return AgentReport(self.name, status, signals, cands,
                           extras={"findings": [f.to_dict() for f in findings]})
