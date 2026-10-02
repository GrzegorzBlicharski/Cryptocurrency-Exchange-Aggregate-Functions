"""SUSTAINABILITY INDEX — burnout / overload protection.

When load becomes unsustainable the Orchestrator shrinks the plan instead of adding
pressure. Missing components are dropped and weights renormalized; with fewer than
two components the index is unknown and capacity stays at the normal default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BANDS = (
    (70, "SUSTAINABLE", 1.00),
    (55, "STRAINED", 0.85),
    (40, "OVERLOADED", 0.65),
    (0, "CRITICAL", 0.45),
)

WEIGHTS = {"sleep": 0.30, "recovery": 0.20, "energy": 0.15, "workload": 0.20, "training_load": 0.10, "rest": 0.05}


def sleep_score(ratio: float) -> float:
    """Sleep vs target. Short sleep is penalized steeply: 90% of target -> 75, 80% -> 50."""
    return 100.0 if ratio >= 1 else max(0.0, 100 - (1 - ratio) * 250)


@dataclass
class Sustainability:
    index: float | None
    band: str
    capacity_factor: float
    components: dict = field(default_factory=dict)
    drivers: list[str] = field(default_factory=list)  # human-readable top negatives

    @property
    def protective(self) -> bool:
        return self.band in ("OVERLOADED", "CRITICAL")


def compute(
    sleep_ratio: float | None,
    bedtime_std_min: float | None,
    recovery_subjective: float | None,
    energy: float | None,
    focused_min_per_day_7d: float | None,
    capacity_min: int,
    acwr: float | None,
    rest_days_7d: int | None,
) -> Sustainability:
    c: dict[str, float] = {}
    if sleep_ratio is not None:
        s = sleep_score(sleep_ratio)
        if bedtime_std_min and bedtime_std_min > 60:
            s -= min(20, (bedtime_std_min - 60) / 3)
        c["sleep"] = max(0.0, s)
    if recovery_subjective is not None:
        c["recovery"] = recovery_subjective * 10
    if energy is not None:
        c["energy"] = energy * 10
    if focused_min_per_day_7d is not None:
        r = focused_min_per_day_7d / capacity_min
        c["workload"] = 100.0 if r <= 1 else max(0.0, 100 - (r - 1) / 0.6 * 100)
    if acwr is not None:
        c["training_load"] = 100.0 if 0.8 <= acwr <= 1.3 else 70.0 if acwr < 0.8 else 60.0 if acwr <= 1.5 else 30.0
    if rest_days_7d is not None:
        c["rest"] = 100.0 if rest_days_7d >= 1 else 40.0

    if len(c) < 2:
        return Sustainability(None, "UNKNOWN", 1.0, c, ["insufficient data: log sleep, energy and workload"])

    total_w = sum(WEIGHTS[k] for k in c)
    index = round(sum(v * WEIGHTS[k] for k, v in c.items()) / total_w, 1)
    band, factor = next((b, f) for th, b, f in BANDS if index >= th)
    drivers = [f"{k.replace('_', ' ')} {v:.0f}/100" for k, v in sorted(c.items(), key=lambda kv: kv[1]) if v < 60][:3]
    return Sustainability(index, band, factor, {k: round(v, 1) for k, v in c.items()}, drivers)
