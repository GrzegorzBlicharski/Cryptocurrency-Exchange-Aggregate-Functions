"""Research & Opportunity Agent (APEX RADAR): only high expected-value items reach you."""
from __future__ import annotations

from ..engines import radar
from ..engines.insights import observation
from ..models import ResearchItem
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why


class ResearchAgent(Agent):
    name = "research"
    domain = "research"
    label = "Radar"
    scopes = frozenset({"research_items"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        items = ctx.query(ResearchItem).filter(ResearchItem.status != "dismissed").all()
        passing = sorted([i for i in items if radar.passes(i.expected_value)], key=lambda i: -i.expected_value)
        signals, cands = [], []
        for i in [i for i in passing if i.status == "new"][:3]:
            signals.append(Signal(
                kind="OPPORTUNITY", severity=4 if i.expected_value >= 50 else 3, key=f"research:item:{i.id}",
                inbox_kind="OPPORTUNITY", title=f"Radar · {i.category}: {i.title} (EV {i.expected_value:.0f})",
                so_what=f"Relevance {i.relevance}/5, impact {i.impact}/5, evidence {i.evidence}/5; "
                        f"cost {i.time_cost_h:g} h / {i.money_cost:g} €. Keep or dismiss.",
                insight=observation(f"EV {i.expected_value} from {i.source}", 1, ["research_items"]),
            ))
        kept = [i for i in passing if i.status == "kept"]
        if kept:
            top = kept[0]
            cands.append(CandidateAction(
                key=f"research:act:{top.id}", agent=self.name, domain="career" if top.category in (
                    "CAREER", "CERTIFICATIONS", "NETWORKING") else "learning",
                title=f"First step on: {top.title}", detail="Do the smallest concrete step (enrol, read ch. 1, "
                "book the slot, try the tool on one real task).", minutes=int(min(45, max(15, top.time_cost_h * 10))),
                impact=min(5, max(2, round(top.expected_value / 20))), urgency=2, energy_cost=2, confidence=0.5,
                why=Why(data_used=["research_items"], reasoning=f"Highest-EV item you kept (EV {top.expected_value}).",
                        expected_benefit="Turns a saved opportunity into progress instead of a bookmark.",
                        confidence="low", downside="Time taken from core training."),
            ))
        status = DomainStatus(self.domain, self.label, None,
                              f"{len(passing)} high-EV items · {len(items) - len(passing)} filtered out",
                              ("Top: " + passing[0].title) if passing else "Nothing above the bar — that's fine.",
                              "unknown")
        status.metrics = {"passing": len(passing), "filtered": len(items) - len(passing), "kept": len(kept)}
        return AgentReport(self.name, status, signals, cands)
