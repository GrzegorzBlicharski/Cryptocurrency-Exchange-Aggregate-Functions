"""Anti-nagging: proactive triage + NOTIFICATION BUDGET.

Disposition ladder: IGNORE < LOG < INFORM < RECOMMEND < PREPARE_ACTION < REQUEST_APPROVAL.
Only the interrupt-worthy cases spend budget; everything else is aggregated into the
Inbox digest and the briefs. No motivational messages, ever.
"""
from __future__ import annotations

from datetime import date

from ..agents.base import Signal

DISPOSITIONS = ("IGNORE", "LOG", "INFORM", "RECOMMEND", "PREPARE_ACTION", "REQUEST_APPROVAL")


def disposition(sig: Signal, today: date) -> str:
    days_left = (sig.deadline - today).days if sig.deadline else None
    if sig.proposed_action and (sig.needs_decision or sig.severity >= 4):
        return "PREPARE_ACTION"
    if sig.needs_decision:
        return "REQUEST_APPROVAL"
    if sig.severity >= 4 or (days_left is not None and days_left <= 3):
        return "RECOMMEND"
    if sig.severity == 3:
        return "INFORM"
    if sig.severity == 2:
        return "LOG"
    return "IGNORE"


def interrupt_worthy(sig: Signal, today: date) -> bool:
    """Urgent, deadline, significant problem, exceptional opportunity or decision needed."""
    days_left = (sig.deadline - today).days if sig.deadline else None
    return (
        sig.severity >= 5
        or (days_left is not None and days_left <= 2)
        or (sig.needs_decision and sig.severity >= 4)
        or (sig.kind == "OPPORTUNITY" and sig.severity >= 5)
        or (sig.kind in ("PROBLEM", "ANOMALY") and sig.severity >= 4)
    )


def inbox_priority(sig: Signal, today: date) -> float:
    days_left = (sig.deadline - today).days if sig.deadline else None
    p = sig.severity * 20.0
    if days_left is not None:
        p += max(0, 10 - days_left) * 3
    if sig.needs_decision:
        p += 10
    return min(p, 130.0)
