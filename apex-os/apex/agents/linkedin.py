"""LinkedIn Intelligence Agent: profile vs. real market requirements.

Works from a snapshot the user pastes (APEX never logs into or scrapes LinkedIn).
It proposes; publishing is a FORBIDDEN_AUTO action — the user edits LinkedIn themselves.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from ..engines.insights import observation
from ..models import CareerOpportunity, LinkedInProfile, Skill
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient


def market_keywords(opps: list[CareerOpportunity], top: int = 12) -> list[tuple[str, int]]:
    c = Counter()
    for o in opps:
        for r in o.requirements or []:
            name = str(r.get("skill", "")).strip()
            if name:
                c[name] += 2 if r.get("required", True) else 1
    return c.most_common(top)


class LinkedInAgent(Agent):
    name = "linkedin"
    domain = "career"
    label = "LinkedIn"
    scopes = frozenset({"linkedin_profile", "career_opportunities", "skills"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        p = ctx.query(LinkedInProfile).order_by(LinkedInProfile.id.desc()).first()
        if not p:
            st = insufficient("linkedin", self.label, "Paste your LinkedIn headline/About/skills on the LinkedIn page.")
            return AgentReport(self.name, st)
        opps = ctx.query(CareerOpportunity).filter(CareerOpportunity.status.in_(("new", "shortlisted", "applied"))).all()
        kws = market_keywords(opps)
        skills = {s.name.lower(): s.level for s in ctx.query(Skill).all()}
        text = " ".join([p.headline, p.about, p.experience, p.skills_text]).lower()
        present = [k for k, _ in kws if k.lower() in text]
        missing_have = [k for k, _ in kws if k.lower() not in text and skills.get(k.lower(), 0) >= 2]
        missing_lack = [k for k, _ in kws if k.lower() not in text and skills.get(k.lower(), 0) < 2]
        coverage = len(present) / len(kws) if kws else None
        checks = {"headline": len(p.headline) >= 40, "about": len(p.about) >= 600,
                  "experience": len(p.experience) >= 300, "skills": len(p.skills_text) >= 40,
                  "activity": p.activity_posts_90d >= 2}
        upd = p.updated_at if p.updated_at.tzinfo else p.updated_at.replace(tzinfo=timezone.utc)
        stale = datetime.now(timezone.utc) - upd > timedelta(days=90)
        score = round(100 * (0.6 * (coverage if coverage is not None else 0.5) + 0.4 * sum(checks.values()) / 5), 1)

        signals, cands = [], []
        if missing_have:
            signals.append(Signal(
                kind="OPPORTUNITY", severity=3, key="linkedin:missing-keywords", inbox_kind="RECOMMENDATION",
                title=f"LinkedIn hides skills the market asks for: {', '.join(missing_have[:4])}",
                so_what="You have these (per your skills list) but recruiters searching for them won't find you.",
                insight=observation(f"{len(present)}/{len(kws)} market keywords on profile", len(opps),
                                    ["linkedin_profile", "career_opportunities", "skills"]),
                proposed_action={"action_type": "draft_linkedin_update", "payload": {"profile_id": p.id},
                                 "reversible": True},
            ))
        weak = [k for k, ok in checks.items() if not ok]
        if weak or missing_have or stale:
            cands.append(CandidateAction(
                key="linkedin:update", agent=self.name, domain="career",
                title="Update LinkedIn: " + ", ".join((missing_have[:3] or weak[:3]) or ["refresh"]),
                detail="Review APEX's draft in Approvals, edit to your voice, then change LinkedIn yourself.",
                minutes=25, impact=3, urgency=2, energy_cost=2, confidence=0.55,
                why=Why(data_used=["linkedin_profile", "career_opportunities", "skills"],
                        reasoning=(f"Keyword coverage {coverage:.0%}." if coverage is not None else "No target roles yet.")
                        + (f" Weak sections: {', '.join(weak)}." if weak else "") + (" Profile >90 days old." if stale else ""),
                        expected_benefit="More recruiter search hits for the roles you target.", confidence="low",
                        alternatives=["Skip until you have 3+ target roles"],
                        downside="Truthfulness matters: only add skills you can evidence."),
            ))
        status = DomainStatus("linkedin", self.label, score,
                              f"Keyword coverage {coverage:.0%}" if coverage is not None else "No market data yet",
                              ("Missing (you have): " + ", ".join(missing_have[:3])) if missing_have else
                              ("To acquire: " + ", ".join(missing_lack[:3])) if missing_lack else "Profile matches targets.",
                              "unknown")
        status.metrics = {"market_keywords": [k for k, _ in kws], "present": present, "missing_have": missing_have,
                          "missing_lack": missing_lack, "checks": checks, "stale": stale}
        return AgentReport(self.name, status, signals, cands)
