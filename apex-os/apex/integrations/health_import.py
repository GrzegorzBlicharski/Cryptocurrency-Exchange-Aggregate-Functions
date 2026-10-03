"""Health data import: Apple Health export.xml (streamed), plus the generic CSV importer.

Maps: StepCount → movement.steps (daily sum), SleepAnalysis (asleep states) → sleep minutes
(attributed to the wake-up day), RestingHeartRate / HRV SDNN → recovery (daily mean; existing
subjective ratings are never overwritten or invented). Google Fit / Health Connect / wearables:
export to CSV and use Settings → CSV import with the field names shown there.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime
from typing import IO

from sqlalchemy.orm import Session

from ..audit import audit, publish
from ..config import get_settings
from ..models import Movement, Recovery, Sleep

STEPS = "HKQuantityTypeIdentifierStepCount"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
RHR = "HKQuantityTypeIdentifierRestingHeartRate"
HRV = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
ASLEEP = ("HKCategoryValueSleepAnalysisAsleep",)  # prefix match covers Core/Deep/REM/Unspecified


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


def parse_apple_health(fh: IO[bytes], since: date | None = None) -> dict:
    tz = get_settings().tz
    steps: dict[date, float] = defaultdict(float)
    sleep: dict[date, list[tuple[datetime, datetime]]] = defaultdict(list)
    rhr: dict[date, list[float]] = defaultdict(list)
    hrv: dict[date, list[float]] = defaultdict(list)
    for _, el in ET.iterparse(fh, events=("end",)):
        if el.tag != "Record":
            el.clear() if el.tag != "HealthData" else None
            continue
        t = el.get("type")
        try:
            start, end = _dt(el.get("startDate")).astimezone(tz), _dt(el.get("endDate")).astimezone(tz)
        except (TypeError, ValueError):
            el.clear()
            continue
        if since and end.date() < since:
            el.clear()
            continue
        if t == STEPS:
            steps[start.date()] += float(el.get("value", 0))
        elif t == SLEEP and str(el.get("value", "")).startswith(ASLEEP):
            sleep[end.date()].append((start, end))
        elif t == RHR:
            rhr[start.date()].append(float(el.get("value", 0)))
        elif t == HRV:
            hrv[start.date()].append(float(el.get("value", 0)))
        el.clear()
    sleep_min = {}
    for d, spans in sleep.items():  # merge overlapping intervals (multiple sources)
        spans.sort()
        total, cs, ce = 0.0, None, None
        for a, b in spans:
            if ce is None or a > ce:
                if ce is not None:
                    total += (ce - cs).total_seconds()
                cs, ce = a, b
            else:
                ce = max(ce, b)
        if ce is not None:
            total += (ce - cs).total_seconds()
        sleep_min[d] = (int(total // 60), spans[0][0].strftime("%H:%M"), max(b for _, b in spans).strftime("%H:%M"))
    return {"steps": dict(steps), "sleep": sleep_min,
            "rhr": {d: sum(v) / len(v) for d, v in rhr.items()}, "hrv": {d: sum(v) / len(v) for d, v in hrv.items()}}


def import_apple_health(db: Session, fh: IO[bytes], since: date | None = None) -> dict:
    data = parse_apple_health(fh, since)
    src = "import:apple_health"
    for d, n in data["steps"].items():
        row = db.query(Movement).filter_by(day=d).first() or Movement(day=d, source=src)
        row.steps = int(n)
        db.add(row)
    for d, (mins, bed, wake) in data["sleep"].items():
        if mins < 60:
            continue
        row = db.query(Sleep).filter_by(day=d).first() or Sleep(day=d, source=src, duration_min=mins)
        row.duration_min, row.bed_time, row.wake_time = mins, bed, wake
        db.add(row)
    for d in set(data["rhr"]) | set(data["hrv"]):
        row = db.query(Recovery).filter_by(day=d).first() or Recovery(day=d, source=src)
        if d in data["rhr"]:
            row.resting_hr = int(round(data["rhr"][d]))
        if d in data["hrv"]:
            row.hrv_ms = round(data["hrv"][d], 1)
        db.add(row)
    counts = {k: len(v) for k, v in data.items()}
    audit(db, "user", "import.apple_health", **counts)
    publish(db, "data.imported", source="apple_health")
    db.commit()
    return counts
