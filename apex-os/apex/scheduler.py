"""In-process scheduler. Each job runs at most once per period (job_runs unique key),
so restarts and multiple ticks never duplicate a brief or review."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from . import memory, reviews
from .integrations import calendar_ics, mail_imap, notify, radar_sync
from .integrations import jobs as jobs_src
from .agents.orchestrator import Orchestrator
from .config import get_settings
from .db import session_scope
from .models import JobRun

log = logging.getLogger("apex.scheduler")


def _claim(db, job: str, period: str) -> bool:
    try:
        with db.begin_nested():
            db.add(JobRun(job=job, period=period))
        return True
    except IntegrityError:
        return False


def due_jobs(now: datetime) -> list[tuple[str, str, Callable]]:
    s = get_settings()
    day = now.date()
    jobs: list[tuple[str, str, Callable]] = []
    if now.hour >= s.morning_brief_hour:
        jobs.append(("morning_brief", day.isoformat(), lambda db: reviews.morning_brief(db, day)))
        jobs.append(("memory_sweep", day.isoformat(), lambda db: memory.sweep(db, day)))
    hour_key = now.strftime("%Y-%m-%dT%H")
    if s.ics_url:
        jobs.append(("calendar_sync", hour_key, lambda db: calendar_ics.sync(db, day)))
    if s.imap_host:
        jobs.append(("mail_sync", hour_key, lambda db: mail_imap.sync(db, day)))
    if now.hour >= s.morning_brief_hour - 1:  # fresh leads before the brief
        jobs.append(("jobs_sync", day.isoformat(), lambda db: jobs_src.sync(db)))
        jobs.append(("radar_sync", day.isoformat(), lambda db: radar_sync.sync(db)))
    if now.hour >= s.evening_review_hour:
        jobs.append(("evening_cycle", day.isoformat(), lambda db: Orchestrator().run_cycle(db, day, force=True)))
        if now.weekday() == s.weekly_review_weekday:
            jobs.append(("weekly_review", day.isoformat(), lambda db: reviews.weekly_review(db, day)))
        if (day + timedelta(days=1)).day == 1:
            jobs.append(("monthly_review", day.strftime("%Y-%m"), lambda db: reviews.monthly_review(db, day)))
    return jobs


def tick(now: datetime | None = None) -> list[str]:
    """Run whatever is due. Safe to call any number of times; each job has its own transaction."""
    now = now or datetime.now(get_settings().tz)
    ran = []
    for job, period, fn in due_jobs(now):
        try:
            with session_scope() as db:
                if not _claim(db, job, period):
                    continue
                fn(db)
            ran.append(job)
        except Exception as exc:  # a failing job must not kill the loop; record it once
            log.exception("job %s failed", job)
            with session_scope() as db:
                if not db.query(JobRun).filter_by(job=job, period=period).first():
                    db.add(JobRun(job=job, period=period, status="error", detail=str(exc)[:500]))
    # Autonomous missions: each due mission runs in its own transaction; failures never stop the loop.
    try:
        from .missions import runner as mission_runner

        if mission_runner.tick():
            ran.append("missions")
    except Exception:  # pragma: no cover
        log.exception("mission tick failed")
    # Push delivery is not period-bound: anything interrupt-worthy and undelivered goes out now.
    try:
        with session_scope() as db:
            if notify.deliver(db).get("sent"):
                ran.append("notify")
    except Exception:  # pragma: no cover - never let push break the loop
        log.exception("notification delivery failed")
    return ran


async def loop(interval_s: int = 300) -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # pragma: no cover
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval_s)
