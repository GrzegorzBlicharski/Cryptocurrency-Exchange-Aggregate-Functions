"""Productivity & Execution Agent: PLAN vs ACTUAL. Hours are not rewarded by themselves."""
from __future__ import annotations

from collections import Counter
from ..engines.insights import observation
from ..engines.stats import mean
from ..models import DeepWork, PlanItem, Recommendation
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient


class ProductivityAgent(Agent):
    name = "productivity"
    domain = "productivity"
    label = "Execution"
    scopes = frozenset({"plan_items", "deep_work", "recommendations"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        items = [p for p in ctx.rows(PlanItem, days=15) if p.day < ctx.day]  # closed days only
        if len(items) < 3:
            return AgentReport(self.name, insufficient(
                self.domain, self.label, "Plan a few items and mark them done/partial/skipped for 3+ days."))

        credit = {"done": 1.0, "partial": 0.5}
        completion = sum(credit.get(p.status, 0.0) for p in items) / len(items)
        planned = sum(p.planned_min for p in items)
        actual = sum((p.actual_min if p.actual_min is not None else
                      (p.planned_min if p.status == "done" else p.planned_min // 2 if p.status == "partial" else 0))
                     for p in items)
        realism = actual / planned if planned else 1.0
        quality = mean([p.quality for p in items if p.quality])
        skipped = [p for p in items if p.status in ("planned", "skipped")]
        reasons = Counter(p.fail_reason for p in skipped if p.fail_reason)

        # Execution latency: recommendation -> completion, hours.
        lat = []
        for p in items:
            if p.recommendation_id and p.completed_at:
                r = ctx.query(Recommendation).filter_by(id=p.recommendation_id).first()
                if r:
                    lat.append((p.completed_at - r.created_at).total_seconds() / 3600)
        latency = mean(lat)

        dw = ctx.rows(DeepWork, days=14)
        dw_quality = mean([d.quality for d in dw if d.quality])
        unplanned_share = (sum(d.minutes for d in dw if not d.planned) / sum(d.minutes for d in dw)) if dw else None

        signals, cands = [], []
        data = ["plan_items (14d)", "deep_work (14d)"]
        if realism < 0.7 and planned >= 300:
            signals.append(Signal(
                kind="PROBLEM", severity=3, key="productivity:overplanning", inbox_kind="INSIGHT",
                title=f"You execute {realism:.0%} of planned minutes",
                so_what="Plans are systematically too big. APEX now scales daily capacity to what you actually do — "
                        "a smaller plan you finish beats a big plan you abandon.",
                insight=observation(f"planned {planned} min, executed ~{actual} min", len(items), data[:1]),
            ))
        if reasons:
            top, cnt = reasons.most_common(1)[0]
            signals.append(Signal(
                kind="PROBLEM", severity=2, key=f"productivity:fail:{top[:30]}", inbox_kind="INSIGHT",
                title=f"Most common failure reason: '{top}' ({cnt}x)",
                so_what="Fix the recurring cause, not the individual misses.",
                insight=observation(f"'{top}' cited {cnt} times", len(skipped), data[:1]),
            ))
        carried = Counter(p.title for p in skipped)
        for title, n in carried.items():
            if n >= 2:
                cands.append(CandidateAction(
                    key=f"productivity:carry:{title[:40]}", agent=self.name, domain="productivity",
                    title=f"Decide on carried-over task: {title}",
                    detail="Skipped 2+ times. Do a 10-min first step now, shrink it, or drop it deliberately.",
                    minutes=10, impact=2, urgency=2, energy_cost=2, confidence=0.6,
                    why=Why(data_used=data[:1], reasoning=f"'{title}' was skipped {n} times in 14 days.",
                            expected_benefit="Removes a recurring open loop.", confidence="medium"),
                ))
                break

        score = round(100 * (0.7 * completion + 0.3 * min(realism, 1.0)), 1)
        status = DomainStatus(
            self.domain, self.label, score,
            f"Completion {completion:.0%} · realism {realism:.0%}" + (f" · quality {quality:.1f}/5" if quality else ""),
            ("Plans are realistic and executed." if score >= 75 else
             "Execution gap: plan less, protect the #1 priority, finish it first."),
            "flat",
        )
        status.metrics = {"completion": round(completion, 2), "realism": round(realism, 2),
                          "latency_h": round(latency, 1) if latency is not None else None,
                          "quality": quality, "deep_work_quality": dw_quality,
                          "unplanned_deep_work_share": unplanned_share, "items": len(items)}
        return AgentReport(self.name, status, signals, cands, extras={"realism": realism})
