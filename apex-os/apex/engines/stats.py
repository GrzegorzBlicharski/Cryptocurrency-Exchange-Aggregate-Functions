"""Small, dependency-free statistics used by agents.

Everything returns None when the data is insufficient — callers must surface that
state instead of inventing numbers.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from statistics import fmean, pstdev


def mean(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return fmean(xs) if xs else None


def std(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return pstdev(xs) if len(xs) >= 2 else None


def ewma(values: Sequence[float], alpha: float = 0.35) -> float | None:
    """Exponentially weighted mean, most recent last."""
    out = None
    for v in values:
        out = v if out is None else alpha * v + (1 - alpha) * out
    return out


def slope_per_day(points: Sequence[tuple[date, float]]) -> float | None:
    """Least-squares slope (units/day). Needs >= 3 points spanning >= 3 days."""
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(d - t0).days for d, _ in points]
    ys = [v for _, v in points]
    if max(xs) - min(xs) < 3:
        return None
    mx, my = fmean(xs), fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False)) / den


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys, strict=False) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    a, b = zip(*pairs, strict=False)
    ma, mb = fmean(a), fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in pairs) / (sa * sb)


def correlation_p_value(r: float, n: int) -> float:
    """Two-sided p-value for Pearson r via Fisher z (normal approx)."""
    if n < 4 or abs(r) >= 1:
        return 0.0 if abs(r) >= 1 and n >= 4 else 1.0
    z = 0.5 * math.log((1 + r) / (1 - r)) * math.sqrt(n - 3)
    return math.erfc(abs(z) / math.sqrt(2))


def welch(a: Sequence[float], b: Sequence[float]) -> dict | None:
    """Difference of means with Welch t and normal-approx p and Cohen's d."""
    if len(a) < 3 or len(b) < 3:
        return None
    ma, mb = fmean(a), fmean(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    pooled = math.sqrt((va + vb) / 2)
    if se == 0:
        return {"diff": ma - mb, "t": 0.0, "p": 1.0, "d": 0.0, "mean_a": ma, "mean_b": mb}
    t = (ma - mb) / se
    # Normal approximation is conservative enough for a personal decision aid at n>=6.
    p = math.erfc(abs(t) / math.sqrt(2))
    return {
        "diff": ma - mb,
        "t": t,
        "p": p,
        "d": (ma - mb) / pooled if pooled else 0.0,
        "mean_a": ma,
        "mean_b": mb,
    }


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))
