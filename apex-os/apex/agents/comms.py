"""Comms Agent: career-relevant email (headers only) and calendar load."""
from __future__ import annotations

from datetime import timedelta

from ..engines.insights import observation
from ..integrations.calendar_ics import busy_minutes
from ..models import CalendarEvent, MailItem
from .base import Agent, AgentContext, AgentReport, CandidateAction, DomainStatus, Signal, Why

SEVERITY = {"offer": 5, "interview": 4, "deadline": 4, "application": 2, "rejection": 2}


class CommsAgent(Agent):
    name = "comms"
    domain = "comms"
    label = "Mail & calendar"
    scopes = frozenset({"mail_items", "calendar_events"})

    def assess(self, ctx: AgentContext) -> AgentReport:
        mails = [m for m in ctx.rows(MailItem, days=14) if not m.handled]
        today_events = ctx.query(CalendarEvent).filter_by(day=ctx.day).all()
        busy = busy_minutes(today_events)
        signals, cands = [], []
        for m in mails:
            sev = SEVERITY.get(m.category, 1)
            dl = m.deadline if m.deadline and m.deadline >= ctx.day else None
            signals.append(Signal(
                kind="DEADLINE" if dl else "OPPORTUNITY" if m.category in ("offer", "interview") else "TREND",
                severity=sev, key=f"comms:mail:{m.id}",
                inbox_kind="DECISION_REQUIRED" if m.category in ("offer", "interview") else "INSIGHT",
                title=f"Email ({m.category}) from {m.sender_domain}: {(m.subject or '')[:90]}",
                so_what={"offer": "Decide and reply — don't let an offer expire.",
                          "interview": "Reply and schedule preparation.",
                          "deadline": "Put the date in your plan.",
                          "rejection": "Log it; APEX tracks your application funnel.",
                          "application": "Application status update."}.get(m.category, ""),
                deadline=dl, needs_decision=m.category in ("offer", "interview"),
                insight=observation("Classified from subject line only (body not read).", 1, ["mail_items"]),
            ))
            if m.category == "interview":
                cands.append(CandidateAction(
                    key=f"comms:interview:{m.id}", agent=self.name, domain="career",
                    title=f"Prepare interview ({m.sender_domain})",
                    detail="Research the company, 3 STAR stories, 5 role-specific questions, German self-intro.",
                    minutes=60, impact=5, urgency=4, energy_cost=3, opportunity_cost=5, confidence=0.7,
                    deadline=dl,
                    why=Why(data_used=["mail_items"], reasoning="Interview-related email received.",
                            expected_benefit="Interviews are the highest-leverage career events.", confidence="medium"),
                ))
        if busy >= 300:
            signals.append(Signal(
                kind="ANOMALY", severity=3, key=f"comms:meeting-heavy:{ctx.day}", inbox_kind="INSIGHT",
                title=f"Meeting-heavy day: {busy / 60:.1f} h booked",
                so_what="Focus capacity reduced accordingly; keep only the #1 priority.",
                insight=observation(f"{busy} busy minutes from calendar", len(today_events), ["calendar_events"]),
            ))
        upcoming = ctx.query(CalendarEvent).filter(CalendarEvent.day > ctx.day,
                                                   CalendarEvent.day <= ctx.day + timedelta(days=7)).count()
        status = DomainStatus(self.domain, self.label, None,
                              f"{len(mails)} open career emails · {busy} min meetings today",
                              "Interview/offer emails need a decision." if any(
                                  m.category in ("offer", "interview") for m in mails) else "Nothing urgent.",
                              "unknown")
        status.metrics = {"busy_min_today": busy, "open_mails": len(mails), "events_next_7d": upcoming}
        return AgentReport(self.name, status, signals, cands)
