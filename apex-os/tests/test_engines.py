from datetime import date, timedelta

from apex.agents.base import CandidateAction, Why
from apex.agents.orchestrator import allocate
from apex.engines import priority, radar, sustainability, velocity
from apex.engines.insights import Insight, correlation_insight

import pytest

D = date(2026, 10, 2)
W = Why(["x"], "r", "b", "low")


def cand(key, domain, minutes, **kw):
    return CandidateAction(key=key, agent=domain, domain=domain, title=key, detail="", minutes=minutes, why=W, **kw)


def test_sustainability_bands_and_unknown():
    assert sustainability.compute(None, None, None, 7, None, 300, None, None).band == "UNKNOWN"
    good = sustainability.compute(1.0, 30, 8, 8, 200, 300, 1.0, 1)
    bad = sustainability.compute(0.75, 120, 3, 3, 520, 300, 1.7, 0)
    assert good.band == "SUSTAINABLE" and good.capacity_factor == 1.0
    assert bad.band in ("OVERLOADED", "CRITICAL") and bad.capacity_factor < 0.7
    assert bad.drivers


def test_priority_deadline_raises_urgency():
    a = cand("a", "career", 30, urgency=1, deadline=D + timedelta(days=1))
    b = cand("b", "career", 30, urgency=1)
    assert priority.score(a, D)[0] > priority.score(b, D)[0]
    assert priority.score(a, D)[1]["urgency"] == 5


def test_priority_energy_penalized_when_protective():
    c = cand("c", "law", 60, energy_cost=5)
    assert priority.score(c, D, protective=True)[0] < priority.score(c, D)[0]


def test_orchestrator_conflict_resolution_from_brief():
    """German wants 2h, movement missing, sleep worse, career deadline in 2 days."""
    strained = sustainability.compute(0.8, 90, 4, 5, 330, 300, None, 0)
    assert strained.protective or strained.band == "STRAINED"
    german = cand("german:2h", "german", 120, impact=4, energy_cost=4)
    german.smaller = cand("german:min", "german", 30, impact=3, energy_cost=2)
    walk = cand("move:walk", "fitness", 30, cognitive=False, protected=True, energy_cost=1, impact=2)
    sleep = cand("rec:sleep", "recovery", 0, cognitive=False, protected=True, energy_cost=1, impact=4)
    job = cand("career:apply", "career", 60, impact=4, deadline=D + timedelta(days=2), reversibility=2)
    admitted, deferred = allocate([german, walk, sleep, job], D, strained, capacity_left=100)
    keys = [a.action.key for a in admitted]
    assert keys[0] == "career:apply"  # deadline pinned first
    assert {"move:walk", "rec:sleep"} <= set(keys)  # recovery protected
    assert "german:min" in keys and "german:2h" not in keys  # shrunk, not dropped
    assert all(a.note for a in admitted if a.action.key in ("career:apply", "german:min"))


def test_allocate_defers_with_reason_when_over_capacity():
    s = sustainability.compute(1.0, 30, 8, 8, 100, 300, 1.0, 1)
    admitted, deferred = allocate([cand("a", "law", 90), cand("b", "german", 90)], D, s, capacity_left=100)
    assert len(admitted) == 1 and len(deferred) == 1
    assert "capacity" in deferred[0].reason


def test_correlation_is_never_causal_and_needs_data():
    xs = list(range(30))
    ys = [100 - 2 * x for x in xs]
    ins = correlation_insight("phone", "deep work", xs, ys, ["t"])
    assert ins.epistemic == "CORRELATION" and ins.confidence in ("low", "medium")
    assert "not proven cause" in ins.caveat
    assert correlation_insight("a", "b", [1, 2, 3], [3, 2, 1], []) is None  # n too small


def test_insight_rejects_unknown_labels():
    with pytest.raises(ValueError):
        Insight("x", "CAUSE", "high")


def test_velocity_requires_measurements_and_hours():
    v = velocity.compute([(D, 50)], [(D, 600)], D)
    assert not v.known and "measurements" in v.reason
    ms = [(D - timedelta(days=40 - i * 10), 50 + i * 5) for i in range(5)]
    eff = [(D - timedelta(days=i), 60) for i in range(41)]
    v = velocity.compute(ms, eff, D)
    assert v.known and v.units_per_100h > 0


def test_radar_gate():
    assert radar.passes(radar.expected_value(5, 5, 4, 5, 2, 0))
    assert not radar.passes(radar.expected_value(2, 2, 2, 2, 40, 900))
