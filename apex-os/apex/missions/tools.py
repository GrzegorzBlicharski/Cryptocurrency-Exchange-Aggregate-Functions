"""Client-side tools available to mission agents. Each handler is bound to (db, mission, settings).

Least privilege: a role only gets the tools listed in roles.py. Writes are either observations
(memory, findings, leads, plan of the mission itself) or go through autonomy.propose().
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .. import autonomy, memory
from ..agents.orchestrator import Orchestrator
from ..audit import audit, publish
from ..engines import experiments as xp
from ..engines import radar
from ..integrations.claude import LoopContext, ToolError
from ..models import CareerOpportunity, Goal, InboxItem, Mission, ResearchItem
from ..untrusted import sanitize
from .roles import ROLES


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": list(props) if required is None else required}


S, I, N, B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}
STR_LIST = {"type": "array", "items": S}

SCHEMAS: dict[str, tuple[str, dict]] = {
    "get_apex_status": ("APEX overview: score, sustainability band/capacity, next best actions, every domain's "
                        "score + 'so what'. Call this first.", _obj({})),
    "get_domain": ("Detailed metrics, signals and agent proposals for one domain (german, law, career, linkedin, "
                   "recovery, movement, attention, productivity, learning, research, comms).",
                   _obj({"domain": S})),
    "update_plan": ("Replace this mission's work plan. You own it: keep 3-8 concrete steps, status todo|doing|done|"
                    "blocked.", _obj({"steps": {"type": "array", "items": _obj({
                        "title": S, "status": {"type": "string", "enum": ["todo", "doing", "done", "blocked"]},
                        "notes": S})}})),
    "record_progress": ("Record verified progress toward the success criteria (0-100) with the evidence.",
                        _obj({"progress_pct": I, "summary": S})),
    "remember": ("Store a durable fact/learning for future runs of this mission (not secrets).",
                 _obj({"content": S, "confidence": N})),
    "recall": ("Read this mission's stored memories and the user's answers.", _obj({})),
    "add_plan_item": ("Put a concrete task into the user's daily plan (YYYY-MM-DD). Size it to the sustainable "
                      "capacity; executes automatically in autonomous mode, otherwise awaits approval.",
                      _obj({"day": S, "title": S, "domain": S, "minutes": I})),
    "propose_action": ("Request an action APEX cannot take silently: prepare_cv_tailoring {opportunity_id}, "
                       "draft_linkedin_update {profile_id}, or user-only actions (send_application, send_message, "
                       "publish_linkedin, spend_money, legal_commitment...) which always wait for the user.",
                       _obj({"action_type": S, "payload_json": S, "rationale": S})),
    "ask_user": ("Ask the supervisor a question. Use only when genuinely blocked or a decision is theirs. "
                 "blocking=true pauses the mission until answered.", _obj({"question": S, "blocking": B})),
    "schedule_next_run": ("Decide when this mission should wake up next (1-168 hours).",
                          _obj({"hours": I, "reason": S})),
    "complete_mission": ("Declare the success criteria met, with verifiable evidence.", _obj({"evidence": S})),
    "save_finding": ("Save a HIGH expected-value item to the Radar. url must be a page you saw via web_search/"
                     "web_fetch in this run.", _obj({
                         "category": S, "title": S, "url": S, "summary": S, "relevance": I, "impact": I,
                         "evidence": I, "actionability": I, "time_cost_h": N, "money_cost": N})),
    "add_job_lead": ("Record a job opportunity. url must be the posting you read in this run; requirements as "
                     "'Skill:level(1-5)[:optional]' strings; deadline YYYY-MM-DD or empty.", _obj({
                         "title": S, "organization": S, "url": S, "location": S, "deadline": S,
                         "requirements": STR_LIST, "why_it_matters": S, "salary_text": S, "salary_source_url": S})),
    "create_experiment": ("Start a randomized within-person experiment. metric one of: " + ", ".join(xp.METRICS),
                          _obj({"hypothesis": S, "intervention": S, "metric": S, "days": I})),
    "list_missions": ("All missions with status, progress, last run and plan.", _obj({})),
    "create_mission": ("Create a new mission for an unserved goal. role one of: " + ", ".join(ROLES),
                       _obj({"title": S, "role": S, "objective": S, "success_criteria": STR_LIST,
                             "cadence_hours": I, "priority": I, "goal_id": I})),
    "update_mission": ("Change another mission: status active|paused|stopped, priority 1-5, cadence_hours, note.",
                       _obj({"mission_id": I, "status": S, "priority": I, "cadence_hours": I, "note": S})),
}


def tool_defs(names: tuple[str, ...]) -> list[dict]:
    return [{"name": n, "description": SCHEMAS[n][0], "input_schema": SCHEMAS[n][1], "strict": True}
            for n in names if n in SCHEMAS]


class MissionTools:
    def __init__(self, db: Session, mission: Mission, today: date, autonomous: bool):
        self.db, self.m, self.today, self.autonomous = db, mission, today, autonomous
        self.agent = f"mission:{mission.id}"
        self.counter = 0

    def _key(self, kind: str) -> str:
        self.counter += 1
        return f"{self.agent}:{self.m.runs}:{kind}:{self.counter}"

    def handlers(self, names: tuple[str, ...]) -> dict:
        return {n: getattr(self, n) for n in names if hasattr(self, n)}

    # ---------------------------------------------------------------- read
    def get_apex_status(self, inp, ctx: LoopContext):
        plan = Orchestrator().plan(self.db, self.today)
        s = plan.sustainability
        return {"date": self.today.isoformat(), "apex_score": plan.apex_score, "bottleneck": plan.bottleneck,
                "sustainability": {"index": s.index, "band": s.band, "drivers": s.drivers},
                "focus_capacity_min_today": plan.capacity_min, "capacity_left_min": plan.capacity_left_min,
                "next_best_actions": [f"{a.action.title} ({a.action.minutes} min)" for a in plan.admitted],
                "goals": [{"id": g.id, "title": g.title, "domain": g.domain, "weight": g.weight,
                           "deadline": g.deadline} for g in self.db.query(Goal).filter_by(status="active")],
                "domains": {k: {"score": r.status.score, "headline": r.status.headline, "so_what": r.status.so_what}
                            for k, r in plan.reports.items()}}

    def get_domain(self, inp, ctx):
        plan = Orchestrator().plan(self.db, self.today)
        name = {"fitness": "movement", "career_market": "career"}.get(inp["domain"], inp["domain"])
        r = plan.reports.get(name)
        if not r:
            raise ToolError(f"unknown domain; choose from {', '.join(plan.reports)}")
        extras = {k: v for k, v in r.extras.items() if k != "all"}
        out = {"status": r.status.__dict__, "signals": [f"[{s.kind}/{s.severity}] {s.title} — {s.so_what}"
                                                       for s in r.signals],
               "proposals": [f"{c.title} ({c.minutes} min): {c.detail}" for c in r.candidates], "extras": extras}
        return json.dumps(out, default=str, ensure_ascii=False)[:15000]

    def recall(self, inp, ctx):
        recs = memory.recall(self.db, category=f"mission:{self.m.id}", limit=40)
        return [{"when": r.created_at.date().isoformat(), "source": r.source, "content": r.content} for r in recs]

    # ---------------------------------------------------------------- own mission state
    def update_plan(self, inp, ctx):
        steps = inp.get("steps", [])[:12]
        self.m.plan = [{"title": s["title"][:200], "status": s["status"], "notes": s.get("notes", "")[:500]}
                       for s in steps]
        return f"plan saved ({len(steps)} steps)"

    def record_progress(self, inp, ctx):
        self.m.progress_pct = max(0, min(int(inp["progress_pct"]), 100))
        self.m.progress_summary = inp["summary"][:2000]
        return "progress recorded"

    def remember(self, inp, ctx):
        memory.remember(self.db, "long_term", f"mission:{self.m.id}", inp["content"][:2000], self.agent,
                        confidence=max(0.0, min(float(inp.get("confidence", 0.6)), 1.0)), today=self.today)
        return "remembered"

    def schedule_next_run(self, inp, ctx):
        hours = max(1, min(int(inp["hours"]), 168))
        self.m.next_run_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        self._scheduled = True
        return f"next run in {hours} h"

    def complete_mission(self, inp, ctx):
        self.m.status = "achieved"
        self.m.progress_pct = 100
        self.m.progress_summary = inp["evidence"][:2000]
        self._inbox("EXPERIMENT_RESULT", f"Mission achieved: {self.m.title}", inp["evidence"],
                    "Review the evidence; reopen the mission if you disagree.", 75)
        ctx.stop_requested = True
        return "mission marked achieved; the supervisor will review"

    # ---------------------------------------------------------------- acting
    def add_plan_item(self, inp, ctx):
        try:
            day = date.fromisoformat(inp["day"])
        except ValueError:
            raise ToolError("day must be YYYY-MM-DD") from None
        if not (self.today <= day <= self.today + timedelta(days=14)):
            raise ToolError("day must be within the next 14 days")
        plan = Orchestrator().plan(self.db, self.today)
        if day == self.today and plan.sustainability.protective and inp["domain"] not in ("recovery", "fitness"):
            raise ToolError(f"sustainability is {plan.sustainability.band}: only recovery/movement items today")
        act = autonomy.propose(self.db, self.agent, "add_plan_item",
                               {"day": day.isoformat(), "title": f"{inp['title'][:180]}", "domain": inp["domain"],
                                "minutes": max(5, min(int(inp["minutes"]), 180))},
                               idempotency_key=self._key("plan"), preauthorized=self.autonomous)
        return f"plan item {act.status}"

    def propose_action(self, inp, ctx):
        try:
            payload = json.loads(inp.get("payload_json") or "{}")
        except ValueError:
            raise ToolError("payload_json must be a JSON object") from None
        if not isinstance(payload, dict):
            raise ToolError("payload_json must be a JSON object")
        act = autonomy.propose(self.db, self.agent, inp["action_type"][:64], payload,
                               idempotency_key=self._key("act"), preauthorized=self.autonomous)
        if act.status in ("awaiting_approval", "prepared"):
            self._inbox("DECISION_REQUIRED", f"{self.m.title}: {inp['action_type']} needs your approval",
                        inp.get("rationale", ""), "Open Approvals to approve or reject.", 95, action_id=act.id)
        return {"action_id": act.id, "status": act.status, "level": act.level,
                "note": "user-only action: the user performs it after approval" if inp["action_type"]
                in autonomy.FORBIDDEN_AUTO else ""}

    def ask_user(self, inp, ctx):
        self._inbox("DECISION_REQUIRED", f"{self.m.title} asks", inp["question"],
                    "Answer on the mission page.", 90 if inp.get("blocking") else 60)
        if inp.get("blocking"):
            self.m.status = "needs_user"
            self.m.blocked_reason = inp["question"][:500]
            ctx.stop_requested = True
            return "question sent; mission paused until the user answers"
        return "question sent; continue with other work"

    def save_finding(self, inp, ctx):
        url = inp["url"].strip()
        if not ctx.seen_url(url):
            raise ToolError("url was not seen via web_search/web_fetch in this run - open it first")
        if self.db.query(ResearchItem).filter_by(url=url[:500]).first():
            return "already saved"
        c = lambda v: max(1, min(int(v), 5))  # noqa: E731
        summary, flags = sanitize(inp["summary"])
        sc = {k: c(inp[k]) for k in ("relevance", "impact", "evidence", "actionability")}
        t, money = max(0.0, float(inp["time_cost_h"])), max(0.0, float(inp["money_cost"]))
        ev = radar.expected_value(sc["relevance"], sc["impact"], sc["evidence"], sc["actionability"], t, money)
        cat = inp["category"].upper()
        self.db.add(ResearchItem(category=cat if cat in radar.CATEGORIES else "TOOLS", title=inp["title"][:300],
                                 url=url[:500], summary=summary[:2000], time_cost_h=t, money_cost=money,
                                 expected_value=ev, injection_flags=flags, source=self.agent,
                                 retrieved_at=datetime.now(timezone.utc), **sc))
        publish(self.db, "radar.added", by=self.agent)
        return f"saved (EV {ev}{', below the display threshold' if not radar.passes(ev) else ''})"

    def add_job_lead(self, inp, ctx):
        url = inp["url"].strip()
        if not ctx.seen_url(url):
            raise ToolError("url was not seen via web_search/web_fetch in this run - read the posting first")
        if self.db.query(CareerOpportunity).filter_by(url=url[:500]).first():
            return "already recorded"
        reqs = []
        for r in inp.get("requirements", [])[:20]:
            parts = [p.strip() for p in r.split(":")]
            try:
                lvl = max(1, min(int(parts[1]), 5)) if len(parts) > 1 else 3
            except ValueError:
                lvl = 3
            if parts[0]:
                reqs.append({"skill": parts[0][:128], "level": lvl,
                             "required": not (len(parts) > 2 and parts[2].lower().startswith("opt"))})
        try:
            deadline = date.fromisoformat(inp["deadline"]) if inp.get("deadline") else None
        except ValueError:
            deadline = None
        sal_src = inp.get("salary_source_url", "").strip()
        desc, flags = sanitize(inp.get("why_it_matters", ""))
        self.db.add(CareerOpportunity(
            title=inp["title"][:200], organization=inp["organization"][:200], location=inp["location"][:128],
            url=url[:500], deadline=deadline, requirements=reqs, description=desc, injection_flags=flags,
            salary_text=inp.get("salary_text", "")[:128] if sal_src and ctx.seen_url(sal_src) else "",
            salary_source=sal_src[:300] if sal_src and ctx.seen_url(sal_src) else "",
            source=self.agent, retrieved_at=datetime.now(timezone.utc), strategic_fit=3))
        publish(self.db, "career.changed", by=self.agent)
        return "lead recorded; the Career agent will score it"

    def create_experiment(self, inp, ctx):
        try:
            e = xp.create(self.db, inp["hypothesis"][:300], self.m.role, inp["intervention"][:300], inp["metric"],
                          self.today, max(6, min(int(inp["days"]), 42)))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        self._inbox("INSIGHT", f"Experiment started: {inp['hypothesis'][:120]}", inp["intervention"],
                    "Follow the treatment/control schedule on the Experiments page.", 55)
        return f"experiment {e.id} started"

    # ---------------------------------------------------------------- chief of staff
    def list_missions(self, inp, ctx):
        return [{"id": m.id, "title": m.title, "role": m.role, "status": m.status, "priority": m.priority,
                 "progress_pct": m.progress_pct, "progress": m.progress_summary[:300], "runs": m.runs,
                 "last_run_at": m.last_run_at, "cadence_hours": m.cadence_hours,
                 "plan": [f"[{s['status']}] {s['title']}" for s in (m.plan or [])]}
                for m in self.db.query(Mission).order_by(Mission.priority.desc()).all()]

    def create_mission(self, inp, ctx):
        from .manager import create

        if inp["role"] not in ROLES or inp["role"] == "chief_of_staff":
            raise ToolError("invalid role")
        try:
            m = create(self.db, title=inp["title"], role=inp["role"], objective=inp["objective"],
                       criteria=inp["success_criteria"], cadence_hours=inp["cadence_hours"],
                       priority=inp["priority"], goal_id=inp.get("goal_id") or None, created_by=self.agent,
                       parent_id=self.m.id)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return f"mission {m.id} created"

    def update_mission(self, inp, ctx):
        m = self.db.get(Mission, int(inp["mission_id"]))
        if not m or m.id == self.m.id:
            raise ToolError("unknown mission (or yourself)")
        if inp.get("status") in ("active", "paused", "stopped"):
            if m.status == "needs_user" and inp["status"] == "active":
                raise ToolError("that mission waits for the user; it cannot be resumed by an agent")
            m.status = inp["status"]
        if inp.get("priority"):
            m.priority = max(1, min(int(inp["priority"]), 5))
        if inp.get("cadence_hours"):
            m.cadence_hours = max(6, min(int(inp["cadence_hours"]), 336))
        if inp.get("note"):
            memory.remember(self.db, "long_term", f"mission:{m.id}", f"Chief of Staff: {inp['note'][:500]}",
                            self.agent, confidence=0.8, today=self.today)
        audit(self.db, self.agent, "mission.update", f"mission:{m.id}", status=m.status)
        return f"mission {m.id} updated"

    # ---------------------------------------------------------------- helper
    def _inbox(self, kind, title, body, so_what, priority, action_id=None):
        from ..config import get_settings

        st = get_settings()
        start = datetime.combine(self.today, datetime.min.time(), tzinfo=st.tz).astimezone(timezone.utc)
        used = self.db.query(InboxItem).filter(InboxItem.interrupt.is_(True), InboxItem.created_at >= start).count()
        interrupt = priority >= 90 and used < st.interrupt_budget_per_day  # same anti-nagging budget
        self.db.add(InboxItem(kind=kind, agent=self.agent, title=title[:200], body=body[:2000], so_what=so_what,
                              priority=priority, action_id=action_id,
                              dedupe_key=f"{self._key('inbox')}@{self.today.isoformat()}",
                              interrupt=interrupt,
                              why={"insight": None, "disposition": "mission", "budget": "mission"}))
