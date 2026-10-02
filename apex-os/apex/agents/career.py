"""Career Intelligence Agent: HIGH-SIGNAL opportunities only, with explicit analysis."""
from __future__ import annotations

from collections import defaultdict

from ..engines.insights import observation
from ..models import Application, CareerOpportunity, Goal, Skill
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why, insufficient

HIGH_SIGNAL_MATCH = 0.6
HIGH_SIGNAL_FIT = 4


def match(opp: CareerOpportunity, skills: dict[str, int]) -> tuple[float, list[dict]]:
    """Weighted requirement coverage (required x2) and the list of gaps."""
    reqs = opp.requirements or []
    if not reqs:
        return 0.0, []
    tot = got = 0.0
    gaps = []
    for r in reqs:
        name = str(r.get("skill", "")).strip()
        need = max(1, int(r.get("level", 3)))
        w = 2.0 if r.get("required", True) else 1.0
        have = skills.get(name.lower(), 0)
        tot += w
        got += w * min(have / need, 1.0)
        if have < need:
            gaps.append({"skill": name, "required_level": need, "current_level": have,
                         "required": bool(r.get("required", True))})
    return got / tot, gaps


def analyze(opp: CareerOpportunity, skills: dict[str, int], goals: list[Goal], today) -> dict:
    m, gaps = match(opp, skills)
    days_left = (opp.deadline - today).days if opp.deadline else None
    career_goals = [g.title for g in goals if g.domain == "career" and g.status == "active"]
    why = (f"Strategic fit {opp.strategic_fit}/5"
           + (f"; supports goal: {career_goals[0]}" if career_goals else ""))
    req_gaps = [g for g in gaps if g["required"]]
    if days_left is not None and days_left < 0:
        action = "Deadline passed — archive."
    elif m >= HIGH_SIGNAL_MATCH and not req_gaps:
        action = "Tailor CV to this role and apply (you submit — APEX never sends)."
    elif req_gaps:
        action = f"Decide: apply now vs. close '{req_gaps[0]['skill']}' gap first."
    else:
        action = "Review and decide whether to shortlist."
    return {
        "id": opp.id,
        "title": opp.title,
        "organization": opp.organization,
        "match": round(m, 2),
        "why_it_matters": why,
        "skill_gap": gaps,
        "salary": ({"text": opp.salary_text, "source": opp.salary_source}
                   if opp.salary_text and opp.salary_source else None),
        "salary_note": "" if opp.salary_source or not opp.salary_text else "salary hidden: no verifiable source",
        "required_action": action,
        "deadline": opp.deadline.isoformat() if opp.deadline else None,
        "days_left": days_left,
        "high_signal": m >= HIGH_SIGNAL_MATCH or opp.strategic_fit >= HIGH_SIGNAL_FIT,
        "source": opp.source,
        "retrieved_at": opp.retrieved_at.isoformat() if opp.retrieved_at else None,
        "injection_flags": opp.injection_flags or [],
    }


class CareerAgent(Agent):
    name = "career"
    domain = "career"
    label = "Career"
    scopes = frozenset({"career_opportunities", "applications", "skills", "goals"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        skills = {s.name.lower(): s.level for s in ctx.query(Skill).all()}
        goals = ctx.query(Goal).all()
        opps = ctx.query(CareerOpportunity).filter(CareerOpportunity.status.in_(("new", "shortlisted"))).all()
        apps = ctx.query(Application).all()
        if not opps and not apps:
            return AgentReport(self.name, insufficient(
                self.domain, self.label, "Add your skills and at least one target role/offer."))

        analyses = [analyze(o, skills, goals, ctx.day) for o in opps]
        live = [a for a in analyses if a["days_left"] is None or a["days_left"] >= 0]
        high = sorted([a for a in live if a["high_signal"]], key=lambda a: -a["match"])
        filtered_out = len(live) - len(high)

        signals, cands = [], []
        for a in high:
            dl = a["days_left"]
            if dl is not None and dl <= 7:
                sev = 5 if dl <= 2 else 4
                signals.append(Signal(
                    kind="DEADLINE", severity=sev, key=f"career:deadline:{a['id']}", inbox_kind="DECISION_REQUIRED",
                    title=f"{a['title']} @ {a['organization']}: deadline in {dl} d (match {a['match']:.0%})",
                    so_what=a["required_action"], deadline=ctx.day.fromisoformat(a["deadline"]),
                    needs_decision=True,
                    insight=observation(f"Match {a['match']:.0%}; {len(a['skill_gap'])} gaps", 1,
                                        ["career_opportunities", "skills"]),
                    proposed_action={"action_type": "prepare_cv_tailoring", "payload": {"opportunity_id": a["id"]},
                                     "reversible": True},
                ))
            else:
                signals.append(Signal(
                    kind="OPPORTUNITY", severity=3 if a["match"] < 0.75 else 4, key=f"career:lead:{a['id']}",
                    inbox_kind="CAREER_LEAD",
                    title=f"Lead: {a['title']} @ {a['organization']} (match {a['match']:.0%})",
                    so_what=a["required_action"],
                    insight=observation(f"Match {a['match']:.0%}", 1, ["career_opportunities", "skills"]),
                ))

        if high:
            best = high[0]
            dl = best["days_left"]
            urgency = 5 if dl is not None and dl <= 2 else 4 if dl is not None and dl <= 7 else 2
            cands.append(CandidateAction(
                key=f"career:apply:{best['id']}", agent=self.name, domain=self.domain,
                title=f"Prepare application: {best['title']} @ {best['organization']}",
                detail="Review APEX's tailoring plan, adapt CV + cover letter, then submit yourself.",
                minutes=60, impact=4, urgency=urgency, energy_cost=3, opportunity_cost=5 if urgency >= 4 else 3,
                reversibility=2, confidence=0.5,
                deadline=ctx.day.fromisoformat(best["deadline"]) if best["deadline"] else None,
                why=Why(data_used=["career_opportunities", "skills", "goals"],
                        reasoning=f"Highest-match live lead ({best['match']:.0%}). {best['why_it_matters']}."
                                  + (f" Deadline in {dl} days." if dl is not None else ""),
                        expected_benefit="Real career option; deadline makes it non-deferrable.",
                        confidence="medium" if best["match"] >= 0.7 else "low",
                        alternatives=[f"{a['title']} @ {a['organization']}" for a in high[1:3]],
                        downside="Costs focus time that would go to German/Law today."),
            ))

        gap_count: dict[str, list] = defaultdict(list)
        for a in high:
            for g in a["skill_gap"]:
                gap_count[g["skill"]].append(g["required_level"] - g["current_level"])
        if gap_count:
            skill, deltas = max(gap_count.items(), key=lambda kv: (len(kv[1]), sum(kv[1])))
            signals.append(Signal(
                kind="SKILL_GAP", severity=3, key=f"career:gap:{skill.lower()}", inbox_kind="INSIGHT",
                title=f"Market gap: '{skill}' missing in {len(deltas)} of {len(high)} high-signal roles",
                so_what=f"Closing '{skill}' raises your match across multiple roles at once.",
                insight=observation(f"'{skill}' gap in {len(deltas)} roles", len(high), ["career_opportunities", "skills"]),
            ))
            cands.append(CandidateAction(
                key=f"career:gap:{skill.lower()}", agent=self.name, domain=self.domain,
                title=f"Close market gap: {skill}", detail=f"Pick one concrete learning step for '{skill}' (course module, project, certificate).",
                minutes=40, impact=3, urgency=1, energy_cost=3, confidence=0.5,
                why=Why(data_used=["career_opportunities", "skills"],
                        reasoning=f"Required in {len(deltas)} high-signal roles, average gap {sum(deltas) / len(deltas):.1f} levels.",
                        expected_benefit="Raises match for several roles simultaneously.", confidence="low"),
            ))

        top3 = [a["match"] for a in high[:3]]
        score = round(100 * sum(top3) / len(top3), 1) if top3 else None
        active_apps = [a for a in apps if a.status in ("submitted", "interview")]
        if score is None:
            status = DomainStatus(self.domain, self.label, None, f"{len(live)} leads, none high-signal",
                                  "No lead passes the quality filter. Add target roles or update skills.", "unknown",
                                  needs="high-signal opportunities")
        else:
            status = DomainStatus(
                self.domain, self.label, score,
                f"Market fit {score:.0f}% · {len(high)} high-signal leads · {len(active_apps)} active applications",
                (f"Top: {high[0]['title']} ({high[0]['match']:.0%})." if high else "")
                + (f" Biggest gap: {max(gap_count, key=lambda k: len(gap_count[k]))}." if gap_count else ""),
                "unknown",
            )
        status.metrics = {"high_signal": len(high), "filtered_out": filtered_out, "applications": len(active_apps)}
        return AgentReport(self.name, status, signals, cands, extras={"opportunities": high, "all": analyses})
