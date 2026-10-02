"""PRIORITY ENGINE.

score = value x cost_discount, 0..100

value  = 0.28 impact + 0.20 urgency + 0.20 strategic alignment + 0.12 opportunity cost
       + 0.12 confidence + 0.08 reversibility
cost   = time cost and energy cost discount; energy is penalized harder when the
         Sustainability Index is in a protective band.
Deadlines raise urgency automatically.
"""
from __future__ import annotations

from datetime import date

from ..agents.base import CandidateAction

W = {"impact": 0.28, "urgency": 0.20, "alignment": 0.20, "opportunity_cost": 0.12,
     "confidence": 0.12, "reversibility": 0.08}


def effective_urgency(c: CandidateAction, today: date) -> int:
    u = c.urgency
    if c.deadline:
        days = (c.deadline - today).days
        if days <= 1:
            u = 5
        elif days <= 3:
            u = max(u, 4)
        elif days <= 7:
            u = max(u, 3)
    return u


def score(c: CandidateAction, today: date, protective: bool = False) -> tuple[float, dict]:
    u = effective_urgency(c, today)
    align = 0.5 if c.alignment is None else c.alignment
    parts = {
        "impact": c.impact / 5,
        "urgency": u / 5,
        "alignment": align,
        "opportunity_cost": c.opportunity_cost / 5,
        "confidence": c.confidence,
        "reversibility": c.reversibility / 5,
    }
    value = sum(W[k] * v for k, v in parts.items())
    time_norm = min(c.minutes / 120, 1.0)
    energy_norm = (c.energy_cost - 1) / 4
    energy_pen = 0.30 if protective else 0.15
    discount = 1 - 0.12 * time_norm - energy_pen * energy_norm
    s = round(100 * value * discount, 1)
    criteria = {
        "expected_impact": c.impact, "urgency": u, "strategic_alignment": round(align, 2),
        "time_cost_min": c.minutes, "energy_cost": c.energy_cost, "opportunity_cost": c.opportunity_cost,
        "reversibility": c.reversibility, "confidence": c.confidence, "score": s,
    }
    return s, criteria
