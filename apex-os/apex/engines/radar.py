"""APEX RADAR: expected-value gate for opportunities (courses, books, tools, events...).

EV = quality (relevance x impact x evidence x actionability, normalized) / cost.
Only items above the threshold are surfaced — this is not a newsfeed.
"""
from __future__ import annotations

CATEGORIES = ("CAREER", "LEGAL", "GERMAN", "EDUCATION", "CERTIFICATIONS", "AI", "TOOLS", "NETWORKING",
              "COURSES", "BOOKS")
THRESHOLD = 25.0


def expected_value(relevance: int, impact: int, evidence: int, actionability: int,
                   time_cost_h: float, money_cost: float) -> float:
    quality = (relevance * impact * evidence * actionability) / 5 ** 4 * 100
    cost = 1 + time_cost_h / 20 + money_cost / 500
    return round(quality / cost, 1)


def passes(ev: float) -> bool:
    return ev >= THRESHOLD
