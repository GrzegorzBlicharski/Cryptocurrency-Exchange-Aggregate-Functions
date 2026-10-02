"""APEX ORCHESTRATOR — the Chief of Staff.

Does not do the domain work itself. It:
1. runs every subagent with a least-privilege context,
2. computes the Sustainability Index and today's capacity,
3. scores every candidate with the Priority Engine,
4. resolves conflicts under the capacity budget (allocate()),
5. triages signals (IGNORE..REQUEST_APPROVAL) under the notification budget,
6. is the single writer of recommendations, inbox items, actions and memory.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .. import autonomy, memory
from ..audit import audit, last_event_id
from ..config import Settings, get_settings
from ..engines import notifications, priority
from ..engines import sustainability as sus
from ..models import (
    DeepWork, Goal, InboxItem, LearningSession, PlanItem, Recommendation, Recovery, Workout,
)
from .attention import AttentionAgent
from .base import Agent, AgentContext, AgentReport, CandidateAction, Signal
from .career import CareerAgent
from .german import GermanAgent
from .law import LawAgent
from .learning import LearningAgent
from .movement import MovementAgent
from .productivity import ProductivityAgent
from .recovery import RecoveryAgent

AGENTS: list[Agent] = [GermanAgent(), LawAgent(), CareerAgent(), RecoveryAgent(), MovementAgent(),
                       AttentionAgent(), ProductivityAgent(), LearningAgent()]

ORCH_SCOPES = frozenset({"learning_sessions", "deep_work", "recovery", "workouts", "goals", "plan_items"})
MAX_ACTIONS = 6
MAX_COGNITIVE = 4
MAX_PER_DOMAIN = 2
HEALTH_DOMAINS = ("recovery", "fitness")
SCORED_DOMAINS = ("german", "law", "career", "fitness", "recovery", "attention", "productivity")


@dataclass
class Admitted:
    action: CandidateAction
    score: float
    criteria: dict
    note: str = ""


@dataclass
class Deferred:
    action: CandidateAction
    score: float
    criteria: dict
    reason: str


@dataclass
class CyclePlan:
    day: date
    reports: dict[str, AgentReport]
    sustainability: sus.Sustainability
    capacity_min: int
    capacity_left_min: int
    admitted: list[Admitted]
    deferred: list[Deferred]
    signals: list[Signal]
    apex_score: float | None
    apex_explain: str
    bottleneck: str | None
    domain_weights: dict[str, float] = field(default_factory=dict)

    @property
    def top(self) -> Admitted | None:
        return self.admitted[0] if self.admitted else None


# ------------------------------------------------------------------ pure parts
def domain_weights(goals: list[Goal]) -> dict[str, float]:
    w = defaultdict(float)
    for g in goals:
        if g.status == "active":
            w[g.domain] += g.weight
    return dict(w)


def alignment_for(domain: str, weights: dict[str, float]) -> float:
    if not weights:
        return 0.5
    top = max(weights.values())
    a = weights.get(domain, 0.0) / top if top else 0.5
    if domain in HEALTH_DOMAINS:  # health enables every other goal
        a = max(a, 0.5)
    return round(a, 2)


def allocate(cands: list[CandidateAction], today: date, s: sus.Sustainability, capacity_left: int,
             realism_note: str = "") -> tuple[list[Admitted], list[Deferred]]:
    """Conflict resolution under a capacity budget. Pure function — unit-tested directly."""
    protective = s.protective
    seen: set[str] = set()
    scored = []
    for c in cands:
        if c.key in seen:
            continue
        seen.add(c.key)
        sc, crit = priority.score(c, today, protective)
        scored.append((c, sc, crit))

    def pinned(c: CandidateAction) -> bool:
        return bool(c.deadline and (c.deadline - today).days <= 2 and c.impact >= 3)

    def protected_first(c: CandidateAction) -> bool:
        return c.protected and s.band in ("STRAINED", "OVERLOADED", "CRITICAL")

    scored.sort(key=lambda t: (not pinned(t[0]), not protected_first(t[0]), -t[1]))

    admitted: list[Admitted] = []
    deferred: list[Deferred] = []
    per_domain: dict[str, int] = defaultdict(int)
    remaining = capacity_left
    cognitive_n = 0
    for c, sc, crit in scored:
        pin = pinned(c)
        if len(admitted) >= MAX_ACTIONS:
            deferred.append(Deferred(c, sc, crit, f"today's queue is full ({MAX_ACTIONS}); focus beats volume"))
            continue
        if per_domain[c.domain] >= MAX_PER_DOMAIN and not pin:
            deferred.append(Deferred(c, sc, crit, f"already {MAX_PER_DOMAIN} {c.domain} actions today"))
            continue
        chosen, note = c, ("deadline-pinned" if pin else "protected recovery slot" if protected_first(c) else "")
        if protective and c.energy_cost >= 4 and not pin:
            if c.smaller and c.smaller.energy_cost < 4:
                chosen, note = c.smaller, f"shrunk: high energy cost while {s.band.lower()}"
            else:
                deferred.append(Deferred(c, sc, crit, f"high energy cost while {s.band.lower()} — protect recovery"))
                continue
        if chosen.cognitive:
            if cognitive_n >= MAX_COGNITIVE:
                deferred.append(Deferred(c, sc, crit, f"max {MAX_COGNITIVE} focus blocks per day"))
                continue
            if chosen.minutes > remaining:
                if chosen.smaller and chosen.smaller.minutes <= remaining:
                    chosen, note = chosen.smaller, f"shrunk to fit capacity ({remaining} min left)"
                elif pin:
                    note = "deadline-pinned: admitted over capacity — everything else waits"
                else:
                    deferred.append(Deferred(c, sc, crit,
                                             f"exceeds today's sustainable capacity ({max(remaining, 0)} min left)"
                                             + (f"; {realism_note}" if realism_note else "")))
                    continue
            remaining -= chosen.minutes
            cognitive_n += 1
        if chosen is not c:
            sc, crit = priority.score(chosen, today, protective)
        per_domain[c.domain] += 1
        admitted.append(Admitted(chosen, sc, crit, note))
    return admitted, deferred


# ------------------------------------------------------------------ orchestrator
class Orchestrator:
    def __init__(self, settings: Settings | None = None, agents: list[Agent] | None = None):
        self.settings = settings or get_settings()
        self.agents = agents or AGENTS

    def assess_all(self, db: Session, day: date) -> dict[str, AgentReport]:
        reports = {}
        for a in self.agents:
            ctx = AgentContext(db, day, self.settings, a.scopes)
            reports[a.name] = a.assess(ctx)
        return reports

    def plan(self, db: Session, day: date) -> CyclePlan:
        reports = self.assess_all(db, day)
        ctx = AgentContext(db, day, self.settings, ORCH_SCOPES)
        goals = ctx.query(Goal).all()
        weights = domain_weights(goals)

        # --- sustainability inputs
        rec = reports["recovery"].status.metrics
        mov = reports["movement"].status.metrics
        focused_by_day = defaultdict(int)
        for s in ctx.rows(LearningSession, days=7):
            focused_by_day[s.day] += s.minutes
        for d in ctx.rows(DeepWork, days=7):
            focused_by_day[d.day] += d.minutes
        workouts = {w.day for w in ctx.rows(Workout, days=7)}
        rest_flags = {r.day for r in ctx.rows(Recovery, days=7) if r.rest_day}
        has_any = bool(focused_by_day) or bool(workouts)
        rest_days = None
        if has_any:
            rest_days = sum(1 for i in range(1, 8)
                            if (d := day - timedelta(days=i)) in rest_flags
                            or (d not in workouts and focused_by_day.get(d, 0) < 60))
        s = sus.compute(
            sleep_ratio=rec.get("sleep_ratio"),
            bedtime_std_min=rec.get("bedtime_std_min"),
            recovery_subjective=rec.get("subjective_3d"),
            energy=rec.get("energy_7d"),
            focused_min_per_day_7d=(sum(focused_by_day.values()) / 7) if focused_by_day else None,
            capacity_min=self.settings.daily_focus_capacity_min,
            acwr=mov.get("acwr"),
            rest_days_7d=rest_days,
        )

        # --- capacity: sustainability x realism (what you actually execute)
        realism = reports["productivity"].extras.get("realism")
        realism_factor = min(1.0, max(0.6, realism)) if realism else 1.0
        capacity = int(self.settings.daily_focus_capacity_min * s.capacity_factor * realism_factor)
        used_today = focused_by_day.get(day, 0)
        capacity_left = max(0, capacity - used_today)
        realism_note = f"capacity scaled to your {realism:.0%} execution rate" if realism and realism < 1 else ""

        # --- candidates
        # --- bottleneck first: the weakest high-weight domain gets an alignment boost
        gaps = {}
        for r in reports.values():
            st = r.status
            if st.domain in SCORED_DOMAINS and st.score is not None:
                gaps[st.domain] = (1 + weights.get(st.domain, 0) / 5) * (100 - st.score)
        bottleneck = max(gaps, key=gaps.get) if gaps else None

        cands: list[CandidateAction] = []
        for r in reports.values():
            if r.status.needs and r.status.domain in SCORED_DOMAINS:
                cands.append(onboarding_candidate(r))
            for c in r.candidates:
                c.alignment = alignment_for(c.domain, weights)
                if c.domain == bottleneck:
                    c.alignment = min(1.0, c.alignment + 0.15)
                if c.smaller:
                    c.smaller.alignment = c.alignment
                cands.append(c)
        admitted, deferred = allocate(cands, day, s, capacity_left, realism_note)

        # --- APEX score + bottleneck
        num = den = 0.0
        for r in reports.values():
            st = r.status
            if st.domain in SCORED_DOMAINS and st.score is not None:
                w = 1 + weights.get(st.domain, 0) / 5
                num += w * st.score
                den += w
        if s.index is not None:
            num += 2 * s.index
            den += 2
        apex = round(num / den, 1) if den else None
        measured = sum(1 for r in reports.values() if r.status.domain in SCORED_DOMAINS and r.status.score is not None)
        explain = (f"Weighted mean of {measured}/{len(SCORED_DOMAINS)} measured domains + sustainability (x2). "
                   "Goal weights raise a domain's influence. Unmeasured domains are excluded, not guessed.")
        signals = sorted((sg for r in reports.values() for sg in r.signals),
                         key=lambda sg: -notifications.inbox_priority(sg, day))
        return CyclePlan(day, reports, s, capacity, capacity_left, admitted, deferred, signals, apex, explain,
                         bottleneck, weights)

    # -------------------------------------------------------------- the only writer
    def commit(self, db: Session, plan: CyclePlan) -> dict:
        day = plan.day
        stats = {"recommendations": 0, "inbox": 0, "interrupts": 0, "actions": 0}

        kept = {r.key for r in db.query(Recommendation).filter(
            Recommendation.day == day, Recommendation.status.in_(("accepted", "done", "dismissed"))).all()}
        db.query(Recommendation).filter(Recommendation.day == day,
                                        Recommendation.status.in_(("proposed", "deferred"))).delete()
        rank = 0
        for a in plan.admitted:
            if a.action.key in kept:
                continue
            rank += 1
            db.add(self._rec(day, a.action, a.score, a.criteria, rank, "proposed", a.note))
            stats["recommendations"] += 1
        for d in plan.deferred:
            if d.action.key in kept:
                continue
            db.add(self._rec(day, d.action, d.score, d.criteria, None, "deferred", d.reason))
        db.flush()

        # --- proactive triage + notification budget
        start = datetime.combine(day, datetime.min.time(), tzinfo=self.settings.tz).astimezone(timezone.utc)
        interrupts_used = db.query(InboxItem).filter(InboxItem.interrupt.is_(True),
                                                     InboxItem.created_at >= start).count()
        for sg in plan.signals:
            disp = notifications.disposition(sg, day)
            if disp == "IGNORE":
                continue
            if disp == "LOG":
                memory.remember(db, "daily", sg.kind.lower(), sg.title, f"agent:{sg.key.split(':')[0]}",
                                key=sg.key, confidence=_conf(sg.insight.confidence), epistemic=sg.insight.epistemic,
                                today=day)
                continue
            cooldown = 1 if sg.severity >= 4 or sg.deadline else 3
            dedupe_key = f"{sg.key}@{day.isoformat()}"
            recent = db.query(InboxItem).filter(
                InboxItem.dedupe_key.like(f"{sg.key}@%"),
                InboxItem.created_at >= start - timedelta(days=cooldown - 1),
            ).first()
            if recent or db.query(InboxItem).filter_by(dedupe_key=dedupe_key).first():
                continue
            action_id = None
            if sg.proposed_action and disp in ("PREPARE_ACTION", "REQUEST_APPROVAL"):
                pa = sg.proposed_action
                act = autonomy.propose(db, sg.key.split(":")[0], pa["action_type"], pa["payload"],
                                       idempotency_key=f"{sg.key}:{pa['action_type']}",
                                       reversible=pa.get("reversible", True))
                action_id = act.id
                stats["actions"] += 1
            want_interrupt = notifications.interrupt_worthy(sg, day)
            interrupt = want_interrupt and interrupts_used < self.settings.interrupt_budget_per_day
            interrupts_used += int(interrupt)
            stats["interrupts"] += int(interrupt)
            db.add(InboxItem(
                kind=sg.inbox_kind, agent=sg.key.split(":")[0], title=sg.title[:200],
                body=sg.insight.statement, so_what=sg.so_what,
                priority=notifications.inbox_priority(sg, day), interrupt=interrupt,
                dedupe_key=dedupe_key, deadline=sg.deadline, action_id=action_id,
                why={"disposition": disp, "insight": sg.insight.to_dict(),
                     "budget": "interrupt" if interrupt else
                               ("digest (interrupt budget spent)" if want_interrupt else "digest")},
            ))
            stats["inbox"] += 1

        # --- Level-3 automation: only if the user granted add_plan_item
        if autonomy.is_granted(db, "add_plan_item"):
            existing = {p.title for p in db.query(PlanItem).filter_by(day=day).all()}
            for a in plan.admitted[:3]:
                if a.action.title in existing:
                    continue
                rec = db.query(Recommendation).filter_by(day=day, key=a.action.key).first()
                if rec:
                    rec.status = "accepted"  # keeps the row (and the plan item's FK) across re-plans
                autonomy.propose(db, "orchestrator", "add_plan_item",
                                 {"day": day.isoformat(), "title": a.action.title, "domain": a.action.domain,
                                  "minutes": a.action.minutes, "recommendation_id": rec.id if rec else None},
                                 idempotency_key=f"plan:{day}:{a.action.key}")
                stats["actions"] += 1

        # --- memory: working state, decision history, digital twin
        memory.remember(db, "working", "capacity",
                        f"band={plan.sustainability.band} index={plan.sustainability.index} capacity={plan.capacity_min}",
                        "orchestrator", key=day.isoformat(), confidence=0.9, today=day)
        if plan.top:
            t = plan.top
            memory.remember(db, "decision", "daily_priority",
                            f"#1 {t.action.title} (score {t.score}); deferred: "
                            + "; ".join(f"{d.action.title} — {d.reason}" for d in plan.deferred[:4]),
                            "orchestrator", key=day.isoformat(), confidence=t.action.confidence, today=day)
        for f in plan.reports["learning"].extras.get("twin", []):
            memory.remember(db, "learning", f["question"], f["statement"], "agent:learning",
                            key=f"{f['question']}:{f.get('stats', {}).get('best', f['statement'][:40])}",
                            confidence=_conf(f["confidence"]), epistemic=f["epistemic"], today=day)
        for f in plan.reports["attention"].extras.get("findings", []):
            memory.remember(db, "learning", "WHAT DESTROYS FOCUS", f["statement"], "agent:attention",
                            key=f["statement"][:60], confidence=_conf(f["confidence"]), epistemic=f["epistemic"],
                            today=day)
        audit(db, "agent:orchestrator", "cycle.commit", day.isoformat(), **stats)
        return stats

    def run_cycle(self, db: Session, day: date, force: bool = False) -> CyclePlan:
        """Plan always (cheap, read-only); commit only when data changed since last commit."""
        plan = self.plan(db, day)
        ev = last_event_id(db)
        marker = memory.recall(db, layer="working", category="cycle_marker")
        marker = next((m for m in marker if m.key == day.isoformat()), None)
        if force or not marker or marker.content != str(ev):
            self.commit(db, plan)
            memory.remember(db, "working", "cycle_marker", str(ev), "orchestrator", key=day.isoformat(),
                            confidence=1.0, today=day)
            db.commit()
        return plan

    @staticmethod
    def _rec(day, c: CandidateAction, score, criteria, rank, status, note) -> Recommendation:
        why = c.why.to_dict()
        return Recommendation(day=day, agent=c.agent, domain=c.domain, title=c.title[:200], detail=c.detail,
                              minutes=c.minutes, score=score, rank=rank, status=status,
                              deferred_reason=note[:300], criteria=criteria, why=why, key=c.key)


def onboarding_candidate(r: AgentReport) -> CandidateAction:
    """When a domain has no data, the highest-value action is to start measuring it."""
    from .base import Why

    return CandidateAction(
        key=f"{r.agent}:onboard", agent=r.agent, domain=r.status.domain,
        title=f"Start measuring {r.status.label}: {r.status.needs}",
        detail=r.status.so_what, minutes=10, impact=2, urgency=2, energy_cost=1, cognitive=False,
        confidence=0.9,
        why=Why(data_used=[], reasoning=f"No {r.status.label} data, so APEX cannot see progress or problems there.",
                expected_benefit="Turns a blind spot into a measurable domain.", confidence="high",
                downside="A few minutes of logging per day."),
    )


def _conf(label: str) -> float:
    return {"low": 0.3, "medium": 0.6, "high": 0.85}.get(label, 0.5)
