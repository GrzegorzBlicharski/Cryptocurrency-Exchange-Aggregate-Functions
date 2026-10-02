"""Agent contract.

Agents are pure analysts: they read through a scoped AgentContext and return an
AgentReport of proposals. They never write to the database and never touch
integrations. The Orchestrator is the single writer.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..engines.insights import Insight
from ..models import Base


class ScopeViolation(PermissionError):
    pass


class AgentContext:
    """Least-privilege read access: only tables listed in `scopes` are readable."""

    def __init__(self, db: Session, day: date, settings: Settings, scopes: frozenset[str]):
        self._db = db
        self.day = day
        self.settings = settings
        self.scopes = scopes

    def _check(self, model: type[Base]) -> None:
        if model.__tablename__ not in self.scopes:
            raise ScopeViolation(f"agent has no scope for table '{model.__tablename__}'")

    def rows(self, model: type[Base], days: int | None = None, day_attr: str = "day", **filters) -> list[Any]:
        """Rows up to and including ctx.day; optionally only the last `days` days."""
        self._check(model)
        q = self._db.query(model)
        for k, v in filters.items():
            q = q.filter(getattr(model, k) == v)
        col = getattr(model, day_attr, None)
        if col is not None:
            q = q.filter(col <= self.day)
            if days is not None:
                q = q.filter(col > self.day - timedelta(days=days))
            q = q.order_by(col)
        return q.all()

    def query(self, model: type[Base]):
        self._check(model)
        return self._db.query(model)


@dataclass
class Why:
    data_used: list[str]
    reasoning: str
    expected_benefit: str
    confidence: str  # low|medium|high
    alternatives: list[str] = field(default_factory=list)
    downside: str = ""
    insight: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidateAction:
    key: str  # stable identity for dedupe, e.g. "german:speaking-block"
    agent: str
    domain: str
    title: str
    detail: str
    minutes: int
    why: Why
    impact: int = 3  # 1..5 expected effect on verified progress / health
    urgency: int = 2  # 1..5
    energy_cost: int = 3  # 1..5
    cognitive: bool = True  # consumes focus capacity
    opportunity_cost: int = 2  # 1..5 cost of NOT doing it now
    reversibility: int = 5  # 1..5 (5 = trivially reversible)
    confidence: float = 0.6  # 0..1 that the action delivers the impact
    deadline: date | None = None
    protected: bool = False  # recovery/health floor: admitted when sustainability is low
    smaller: "CandidateAction | None" = None  # minimum-effective-dose variant
    alignment: float | None = None  # filled by orchestrator from goals (0..1)


@dataclass
class Signal:
    kind: str  # PROBLEM|OPPORTUNITY|TREND|ANOMALY|DEADLINE|SKILL_GAP
    severity: int  # 1..5
    title: str
    so_what: str
    insight: Insight
    key: str
    inbox_kind: str = "INSIGHT"
    deadline: date | None = None
    needs_decision: bool = False
    proposed_action: dict | None = None  # {"action_type":..., "payload":..., "reversible":...}


@dataclass
class DomainStatus:
    domain: str
    label: str
    score: float | None  # 0..100, None = insufficient data
    headline: str
    so_what: str
    trend: str = "flat"  # up|down|flat|unknown
    metrics: dict = field(default_factory=dict)
    needs: str = ""  # what data is missing if score is None


@dataclass
class AgentReport:
    agent: str
    status: DomainStatus
    signals: list[Signal] = field(default_factory=list)
    candidates: list[CandidateAction] = field(default_factory=list)
    extras: dict = field(default_factory=dict)  # agent-specific views (knowledge graph...)


class Agent:
    name: str = "base"
    domain: str = "other"
    label: str = ""
    scopes: frozenset[str] = frozenset()

    def assess(self, ctx: AgentContext) -> AgentReport:  # pragma: no cover - interface
        raise NotImplementedError


def insufficient(domain: str, label: str, needs: str) -> DomainStatus:
    return DomainStatus(
        domain=domain,
        label=label,
        score=None,
        headline="Not enough data yet",
        so_what=f"APEX cannot judge {label} yet. {needs}",
        trend="unknown",
        needs=needs,
    )


def stable_key(text: str) -> str:
    """Process-independent short hash for dedupe keys (built-in hash() is salted)."""
    return hashlib.sha1(text.encode()).hexdigest()[:10]
