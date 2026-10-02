"""PROGRESS VELOCITY: verified competency change per 100 hours of effort.

A competency unit = one percentage point on the domain's measured scale
(test %, question accuracy %, session accuracy %). Hours without measured
improvement show up as low velocity — which is the point.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .stats import mean


@dataclass
class Velocity:
    units_per_100h: float | None
    baseline: float | None
    current: float | None
    hours: float
    measurements: int
    window_days: int
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.units_per_100h is not None


def compute(
    measurements: list[tuple[date, float]],
    effort: list[tuple[date, int]],
    today: date,
    window_days: int = 60,
    min_measurements: int = 4,
    min_hours: float = 5.0,
) -> Velocity:
    start = today - timedelta(days=window_days)
    ms = sorted((d, v) for d, v in measurements if start < d <= today)
    if len(ms) < min_measurements:
        return Velocity(None, None, None, 0.0, len(ms), window_days,
                        f"need >= {min_measurements} measurements in {window_days} days (have {len(ms)})")
    k = max(2, len(ms) // 4)
    baseline = mean([v for _, v in ms[:k]])
    current = mean([v for _, v in ms[-k:]])
    first_day, last_day = ms[0][0], ms[-1][0]
    hours = sum(m for d, m in effort if first_day <= d <= last_day) / 60.0
    if hours < min_hours:
        return Velocity(None, baseline, current, hours, len(ms), window_days,
                        f"need >= {min_hours:.0f} h of logged effort between measurements (have {hours:.1f} h)")
    return Velocity(round((current - baseline) / hours * 100, 1), baseline, current, round(hours, 1),
                    len(ms), window_days)
