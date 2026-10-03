"""Database schema.

APEX is single-tenant by design: one deployment holds one person's data, which keeps
authorization simple and avoids cross-user leakage. `users` exists for authentication.

Conventions:
- timestamps are timezone-aware UTC (`utcnow`); `day` columns are local dates.
- raw observations carry `source` (manual | import:<x> | integration:<x>).
- sensitive free text uses EncryptedText.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .crypto import EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ObservationMixin(TimestampMixin):
    source: Mapped[str] = mapped_column(String(64), default="manual")


# ---------------------------------------------------------------- identity & security
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128), default="")


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(TimestampMixin, Base):
    """Who/what did what. Never stores sensitive payloads, only identifiers."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))  # user | agent:<name> | system
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict] = mapped_column(default=dict)


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(default=dict)


class AutonomyGrant(TimestampMixin, Base):
    """User pre-authorization of a SAFE action type at level 3."""

    __tablename__ = "autonomy_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    action_type: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class JobRun(TimestampMixin, Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("job", "period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(64))
    period: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="ok")
    detail: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- direction
class Goal(TimestampMixin, Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(32))  # german|law|career|fitness|recovery|attention|productivity|other
    why: Mapped[str] = mapped_column(Text, default="")
    metric: Mapped[str] = mapped_column(String(64), default="")
    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    weight: Mapped[int] = mapped_column(Integer, default=3)  # 1..5 strategic importance
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|done|dropped


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    domain: Mapped[str] = mapped_column(String(32), default="career")
    level: Mapped[int] = mapped_column(Integer, default=0)  # 0 none .. 5 expert (self + evidence)
    evidence: Mapped[str] = mapped_column(Text, default="")


class Habit(TimestampMixin, Base):
    __tablename__ = "habits"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    domain: Mapped[str] = mapped_column(String(32), default="other")
    target_per_week: Mapped[int] = mapped_column(Integer, default=5)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class HabitLog(ObservationMixin, Base):
    __tablename__ = "habit_logs"
    __table_args__ = (UniqueConstraint("habit_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=True)


class PlanItem(TimestampMixin, Base):
    """What was planned for a day vs. what actually happened (Productivity agent)."""

    __tablename__ = "plan_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(32), default="other")
    planned_min: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|done|partial|skipped
    actual_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fail_reason: Mapped[str] = mapped_column(String(200), default="")


# ---------------------------------------------------------------- learning
class LearningSession(ObservationMixin, Base):
    """Common facts for any study session; detail rows hold domain specifics."""

    __tablename__ = "learning_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)  # german|law|other
    day: Mapped[date] = mapped_column(Date, index=True)
    start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0..23 local
    minutes: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(64), default="")  # e.g. flashcards, tutor, past papers
    material: Mapped[str] = mapped_column(String(128), default="")
    breaks: Mapped[int] = mapped_column(Integer, default=0)
    focus: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5 subjective
    fatigue: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5
    distractions: Mapped[int] = mapped_column(Integer, default=0)
    retention_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..100 delayed recall
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)

    language: Mapped["LanguageSession | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )
    law: Mapped["LawSession | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class LanguageSession(Base):
    __tablename__ = "language_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("learning_sessions.id", ondelete="CASCADE"), unique=True)
    language: Mapped[str] = mapped_column(String(16), default="de")
    skill: Mapped[str] = mapped_column(String(16))  # speaking|writing|reading|listening|vocabulary|grammar
    mode: Mapped[str] = mapped_column(String(8), default="active")  # active|passive
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..100 within-session
    session: Mapped[LearningSession] = relationship(back_populates="language")


class LawSession(Base):
    __tablename__ = "law_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("learning_sessions.id", ondelete="CASCADE"), unique=True)
    area: Mapped[str] = mapped_column(String(64))  # e.g. civil, contract, criminal, admin, eu, labour
    activity: Mapped[str] = mapped_column(String(32), default="reading")
    # reading|questions|drafting|contract_analysis|argumentation|research|case_analysis
    session: Mapped[LearningSession] = relationship(back_populates="law")


class Test(ObservationMixin, Base):
    __tablename__ = "tests"
    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    area: Mapped[str] = mapped_column(String(64), default="")  # german skill or law area
    title: Mapped[str] = mapped_column(String(200), default="")
    day: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float, default=100)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def pct(self) -> float:
        return 100.0 * self.score / self.max_score if self.max_score else 0.0


class Question(ObservationMixin, Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), nullable=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    area: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str] = mapped_column(String(128), default="")
    day: Mapped[date] = mapped_column(Date, index=True)
    correct: Mapped[bool] = mapped_column(Boolean)
    time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)


class Error(ObservationMixin, Base):
    """Recurring mistakes (grammar pattern, legal concept confusion...)."""

    __tablename__ = "errors"
    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    area: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    last_seen: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|fixed


# ---------------------------------------------------------------- body & attention
class Sleep(ObservationMixin, Base):
    __tablename__ = "sleep"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)  # the morning you woke up
    bed_time: Mapped[str] = mapped_column(String(5), default="")  # HH:MM local
    wake_time: Mapped[str] = mapped_column(String(5), default="")
    duration_min: Mapped[int] = mapped_column(Integer)
    quality: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5


class Energy(ObservationMixin, Base):
    __tablename__ = "energy"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[int] = mapped_column(Integer)  # 1..10
    stress: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..10


class Recovery(ObservationMixin, Base):
    __tablename__ = "recovery"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)
    subjective: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..10 how recovered (None if only device data)
    soreness: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..10
    resting_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hrv_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rest_day: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)


class Movement(ObservationMixin, Base):
    __tablename__ = "movement"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    active_min: Mapped[int] = mapped_column(Integer, default=0)
    sedentary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    walks: Mapped[int] = mapped_column(Integer, default=0)


class Workout(ObservationMixin, Base):
    __tablename__ = "workouts"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # strength|run|cycling|mobility|walk|other
    minutes: Mapped[int] = mapped_column(Integer)
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..10 perceived exertion

    @property
    def load(self) -> float:
        """Session-RPE training load (minutes x RPE)."""
        return self.minutes * (self.rpe or 5)


class ScreenTime(ObservationMixin, Base):
    __tablename__ = "screen_time"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)
    total_min: Mapped[int] = mapped_column(Integer)
    phone_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    social_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pickups: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offline_min: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DeepWork(ObservationMixin, Base):
    __tablename__ = "deep_work"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer)
    domain: Mapped[str] = mapped_column(String(32), default="other")
    task: Mapped[str] = mapped_column(String(200), default="")
    planned: Mapped[bool] = mapped_column(Boolean, default=True)
    interruptions: Mapped[int] = mapped_column(Integer, default=0)
    context_switches: Mapped[int] = mapped_column(Integer, default=0)
    quality: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5 output quality


# ---------------------------------------------------------------- career
class CareerOpportunity(ObservationMixin, Base):
    __tablename__ = "career_opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    organization: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    # [{"skill": "German", "level": 4, "required": true}, ...]
    requirements: Mapped[list] = mapped_column(default=list)
    salary_text: Mapped[str] = mapped_column(String(128), default="")
    salary_source: Mapped[str] = mapped_column(String(300), default="")
    strategic_fit: Mapped[int] = mapped_column(Integer, default=3)  # 1..5 user judgement
    description: Mapped[str] = mapped_column(Text, default="")  # untrusted external text
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|shortlisted|applied|rejected|archived
    injection_flags: Mapped[list] = mapped_column(default=list)


class Application(TimestampMixin, Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("career_opportunities.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|submitted|interview|offer|rejected
    submitted_at: Mapped[date | None] = mapped_column(Date, nullable=True)  # set by the USER only
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)


class SkillGap(TimestampMixin, Base):
    __tablename__ = "skills_gap"
    __table_args__ = (UniqueConstraint("opportunity_id", "skill"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("career_opportunities.id", ondelete="CASCADE"))
    skill: Mapped[str] = mapped_column(String(128))
    required_level: Mapped[int] = mapped_column(Integer)
    current_level: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- agent loop
class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    agent: Mapped[str] = mapped_column(String(32))
    domain: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = deferred
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|accepted|done|dismissed|deferred
    deferred_reason: Mapped[str] = mapped_column(String(300), default="")
    criteria: Mapped[dict] = mapped_column(default=dict)
    why: Mapped[dict] = mapped_column(default=dict)
    key: Mapped[str] = mapped_column(String(128), default="")


class InboxItem(TimestampMixin, Base):
    __tablename__ = "inbox_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    # INSIGHT|WARNING|OPPORTUNITY|DECISION_REQUIRED|RECOMMENDATION|CAREER_LEAD|EXPERIMENT_RESULT
    agent: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    so_what: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[float] = mapped_column(Float, default=0)
    interrupt: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="unread")  # unread|read|dismissed|done
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    why: Mapped[dict] = mapped_column(default=dict)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("agent_actions.id"), nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentAction(TimestampMixin, Base):
    __tablename__ = "agent_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(32))
    action_type: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    # proposed|prepared|awaiting_approval|approved|executed|rejected|blocked|failed
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[dict] = mapped_column(default=dict)
    result: Mapped[dict] = mapped_column(default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String(32))
    # what distinguishes treatment days, e.g. {"table": "learning_sessions", "field": "start_hour", "op": "<", "value": 12}
    intervention: Mapped[str] = mapped_column(String(300))
    metric: Mapped[str] = mapped_column(String(64))  # registered metric name, see engines/experiments.py
    start_day: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer, default=14)
    status: Mapped[str] = mapped_column(String(16), default="running")  # planned|running|evaluated|stopped
    # list of local dates (ISO) the user marked as intervention days
    treatment_days: Mapped[list] = mapped_column(default=list)
    result: Mapped[dict] = mapped_column(default=dict)
    decision: Mapped[str] = mapped_column(String(16), default="")  # continue|modify|reject


class AgentMemory(Base):
    __tablename__ = "agent_memory"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    layer: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(EncryptedText)
    source: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    relevance: Mapped[float] = mapped_column(Float, default=0.5)
    epistemic: Mapped[str] = mapped_column(String(16), default="OBSERVATION")
    review_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("agent_memory.id"), nullable=True)


class ResearchItem(ObservationMixin, Base):
    """Radar finding. Only stored/shown if expected value passes the gate."""

    __tablename__ = "research_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(500), default="")
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")  # untrusted external text
    relevance: Mapped[int] = mapped_column(Integer, default=3)  # 1..5
    impact: Mapped[int] = mapped_column(Integer, default=3)
    time_cost_h: Mapped[float] = mapped_column(Float, default=1)
    money_cost: Mapped[float] = mapped_column(Float, default=0)
    evidence: Mapped[int] = mapped_column(Integer, default=3)
    actionability: Mapped[int] = mapped_column(Integer, default=3)
    expected_value: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|kept|dismissed
    injection_flags: Mapped[list] = mapped_column(default=list)


# ---------------------------------------------------------------- reviews
class DailyReview(TimestampMixin, Base):
    __tablename__ = "daily_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)
    morning_brief: Mapped[dict] = mapped_column(default=dict)
    evening: Mapped[dict] = mapped_column(default=dict)  # computed planned/done/failed
    why_failed: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    learned: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    change_tomorrow: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    energy_end: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WeeklyReview(TimestampMixin, Base):
    __tablename__ = "weekly_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    content: Mapped[dict] = mapped_column(default=dict)
    user_notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)


class MonthlyReview(TimestampMixin, Base):
    __tablename__ = "monthly_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), unique=True)  # YYYY-MM
    content: Mapped[dict] = mapped_column(default=dict)
    user_notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)


# ---------------------------------------------------------------- integrations
class IntegrationSetting(TimestampMixin, Base):
    """Non-secret integration configuration (feed URLs, keywords, toggles). Secrets live in env only."""

    __tablename__ = "integration_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict] = mapped_column(default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(300), default="")


class LinkedInProfile(TimestampMixin, Base):
    """User-pasted snapshot of their LinkedIn profile. APEX never logs into or scrapes LinkedIn."""

    __tablename__ = "linkedin_profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    headline: Mapped[str] = mapped_column(String(300), default="")
    about: Mapped[str] = mapped_column(Text, default="")
    experience: Mapped[str] = mapped_column(Text, default="")
    skills_text: Mapped[str] = mapped_column(Text, default="")
    activity_posts_90d: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CalendarEvent(ObservationMixin, Base):
    """Busy time from the user's calendar (ICS). Title encrypted; only what capacity planning needs."""

    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("uid", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(255))
    day: Mapped[date] = mapped_column(Date, index=True)
    start_min: Mapped[int] = mapped_column(Integer)  # minutes after local midnight
    end_min: Mapped[int] = mapped_column(Integer)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)


class MailItem(ObservationMixin, Base):
    """Minimal metadata of relevant emails (read-only IMAP). Bodies are never stored."""

    __tablename__ = "mail_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(300), unique=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day: Mapped[date] = mapped_column(Date, index=True)
    sender_domain: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    category: Mapped[str] = mapped_column(String(32))  # interview|deadline|application|offer|rejection|other
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    injection_flags: Mapped[list] = mapped_column(default=list)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
