"""Insight objects with the CAUSALITY GUARDRAIL.

Every important insight is labeled:
  OBSERVATION   - a measured fact ("7-day sleep avg 6.1 h")
  CORRELATION   - two variables move together (r, n, p) - NOT a cause
  HYPOTHESIS    - a plausible causal story worth testing
  TESTED_EFFECT - result of a controlled experiment (only experiments.py may emit this)
and carries a confidence (low | medium | high) derived from data, not from tone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .stats import correlation_p_value, pearson

EPISTEMIC = ("OBSERVATION", "CORRELATION", "HYPOTHESIS", "TESTED_EFFECT")


@dataclass
class Insight:
    statement: str
    epistemic: str
    confidence: str  # low|medium|high
    n: int = 0
    data_used: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    caveat: str = ""

    def __post_init__(self):
        if self.epistemic not in EPISTEMIC:
            raise ValueError(f"unknown epistemic label {self.epistemic}")
        if self.confidence not in ("low", "medium", "high"):
            raise ValueError("confidence must be low|medium|high")

    def to_dict(self) -> dict:
        return asdict(self)


def observation(statement: str, n: int, data_used: list[str], **stats) -> Insight:
    conf = "high" if n >= 14 else "medium" if n >= 5 else "low"
    return Insight(statement, "OBSERVATION", conf, n, data_used, stats)


def correlation_insight(
    x_name: str, y_name: str, xs: list[float], ys: list[float], data_used: list[str]
) -> Insight | None:
    """Return a CORRELATION insight, or None if there is nothing worth saying.

    Confidence never exceeds 'medium' for a correlation: observational data cannot
    establish causality. The caveat always says so.
    """
    pairs = [(x, y) for x, y in zip(xs, ys, strict=False) if x is not None and y is not None]
    n = len(pairs)
    if n < 7:
        return None
    r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    if r is None or abs(r) < 0.3:
        return None
    p = correlation_p_value(r, n)
    conf = "medium" if (n >= 21 and p < 0.01) else "low"
    direction = "higher" if r > 0 else "lower"
    return Insight(
        statement=f"Days with higher {x_name} tend to have {direction} {y_name} (r={r:+.2f}, n={n}).",
        epistemic="CORRELATION",
        confidence=conf,
        n=n,
        data_used=data_used,
        stats={"r": round(r, 3), "p": round(p, 4)},
        caveat="Correlation, not proven cause. Run an experiment to test it.",
    )


def hypothesis(statement: str, basis: Insight | None, data_used: list[str]) -> Insight:
    return Insight(
        statement=statement,
        epistemic="HYPOTHESIS",
        confidence="low" if basis is None else basis.confidence,
        n=basis.n if basis else 0,
        data_used=data_used,
        stats=basis.stats if basis else {},
        caveat="Untested. Treat as a candidate for the Experiment Engine.",
    )
