"""Daily logging: one declarative spec drives the HTML forms, the JSON API and CSV import."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from .audit import audit, publish
from .models import (
    DeepWork, Energy, Error, LanguageSession, LawSession, LearningSession, Movement, Question, Recovery,
    ScreenTime, Sleep, Test, Workout,
)

GERMAN_SKILLS = ["speaking", "writing", "reading", "listening", "vocabulary", "grammar"]
LAW_ACTIVITIES = ["reading", "questions", "drafting", "contract_analysis", "argumentation", "research", "case_analysis"]
DOMAINS = ["german", "law", "career", "fitness", "recovery", "attention", "productivity", "other"]


@dataclass
class F:
    name: str
    type: str = "int"  # int|float|str|text|date|bool|select|time
    label: str = ""
    required: bool = False
    options: list[str] | None = None
    lo: float | None = None
    hi: float | None = None
    default: object = None

    @property
    def title(self) -> str:
        return self.label or self.name.replace("_", " ")


_SESSION = [
    F("day", "date", required=True), F("start_hour", "int", "start hour (0-23)", lo=0, hi=23),
    F("minutes", "int", required=True, lo=1, hi=600),
]
_SESSION_TAIL = [
    F("method", "str"), F("material", "str"), F("breaks", "int", lo=0, hi=50, default=0),
    F("focus", "int", "focus 1-5", lo=1, hi=5), F("fatigue", "int", "fatigue 1-5", lo=1, hi=5),
    F("distractions", "int", lo=0, hi=200, default=0),
    F("retention_score", "float", "delayed recall % (optional)", lo=0, hi=100), F("notes", "text"),
]

SPECS: dict[str, dict] = {
    "german_session": {"label": "German session", "fields": _SESSION + [
        F("skill", "select", required=True, options=GERMAN_SKILLS),
        F("mode", "select", options=["active", "passive"], default="active"),
        F("accuracy", "float", "accuracy % (optional)", lo=0, hi=100)] + _SESSION_TAIL},
    "law_session": {"label": "Law session", "fields": _SESSION + [
        F("area", "str", "area (e.g. contract, civil, criminal)", required=True),
        F("activity", "select", options=LAW_ACTIVITIES, default="reading")] + _SESSION_TAIL},
    "test": {"label": "Test result", "fields": [
        F("domain", "select", required=True, options=["german", "law", "other"]),
        F("area", "str", "skill / area"), F("title", "str"), F("day", "date", required=True),
        F("score", "float", required=True, lo=0), F("max_score", "float", lo=1, default=100),
        F("duration_min", "int", lo=1, hi=600)]},
    "questions": {"label": "Question results (batch)", "fields": [
        F("domain", "select", required=True, options=["law", "german"]),
        F("area", "str", required=True), F("topic", "str"), F("day", "date", required=True),
        F("total", "int", required=True, lo=1, hi=500), F("correct", "int", required=True, lo=0, hi=500),
        F("avg_time_sec", "float", lo=0, hi=3600)]},
    "error": {"label": "Recurring error", "fields": [
        F("domain", "select", required=True, options=["german", "law", "other"]), F("area", "str"),
        F("category", "str", "error pattern", required=True), F("description", "text"),
        F("day", "date", required=True)]},
    "sleep": {"label": "Sleep", "fields": [
        F("day", "date", "morning of", required=True), F("bed_time", "time"), F("wake_time", "time"),
        F("duration_min", "int", "duration (min; auto from times if empty)", lo=0, hi=1000),
        F("quality", "int", "quality 1-5", lo=1, hi=5)]},
    "energy": {"label": "Energy", "fields": [
        F("day", "date", required=True), F("hour", "int", lo=0, hi=23),
        F("level", "int", "energy 1-10", required=True, lo=1, hi=10), F("stress", "int", "stress 1-10", lo=1, hi=10)]},
    "recovery": {"label": "Recovery", "fields": [
        F("day", "date", required=True), F("subjective", "int", "recovered 1-10", required=True, lo=1, hi=10),
        F("soreness", "int", "soreness 1-10", lo=1, hi=10), F("resting_hr", "int", lo=25, hi=220),
        F("hrv_ms", "float", lo=1, hi=400), F("rest_day", "bool", default=False), F("notes", "text")]},
    "movement": {"label": "Movement (daily)", "fields": [
        F("day", "date", required=True), F("steps", "int", required=True, lo=0, hi=150000),
        F("active_min", "int", lo=0, hi=1440, default=0), F("sedentary_min", "int", lo=0, hi=1440),
        F("walks", "int", lo=0, hi=50, default=0)]},
    "workout": {"label": "Workout", "fields": [
        F("day", "date", required=True),
        F("kind", "select", required=True, options=["strength", "run", "cycling", "mobility", "walk", "swim", "other"]),
        F("minutes", "int", required=True, lo=1, hi=600), F("rpe", "int", "effort RPE 1-10", lo=1, hi=10)]},
    "screen_time": {"label": "Screen time", "fields": [
        F("day", "date", required=True), F("total_min", "int", required=True, lo=0, hi=1440),
        F("phone_min", "int", lo=0, hi=1440), F("social_min", "int", lo=0, hi=1440),
        F("pickups", "int", lo=0, hi=2000), F("offline_min", "int", lo=0, hi=1440)]},
    "deep_work": {"label": "Deep work block", "fields": [
        F("day", "date", required=True), F("start_hour", "int", lo=0, hi=23),
        F("minutes", "int", required=True, lo=1, hi=600),
        F("domain", "select", options=DOMAINS, default="other"), F("task", "str"),
        F("planned", "bool", default=True), F("interruptions", "int", lo=0, hi=200, default=0),
        F("context_switches", "int", lo=0, hi=200, default=0), F("quality", "int", "output quality 1-5", lo=1, hi=5)]},
}


class ValidationError(ValueError):
    pass


def parse(kind: str, raw: dict) -> dict:
    if kind not in SPECS:
        raise ValidationError(f"unknown log kind '{kind}'")
    out = {}
    for f in SPECS[kind]["fields"]:
        v = raw.get(f.name)
        if isinstance(v, str):
            v = v.strip()
        if v in (None, ""):
            if f.type == "bool":
                # absent -> default; present but empty (unchecked HTML checkbox) -> False
                out[f.name] = bool(f.default) if f.name not in raw else False
                continue
            if f.required:
                raise ValidationError(f"{f.title} is required")
            out[f.name] = f.default
            continue
        try:
            if f.type == "int":
                v = int(float(v))
            elif f.type == "float":
                v = float(v)
            elif f.type == "date":
                v = v if isinstance(v, date) else date.fromisoformat(str(v))
            elif f.type == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
            elif f.type == "time":
                h, m = str(v).split(":")[:2]
                if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
                    raise ValueError
                v = f"{int(h):02d}:{int(m):02d}"
            elif f.type == "select":
                if v not in (f.options or []):
                    raise ValueError
            else:
                v = str(v)[:2000 if f.type == "text" else 200]
        except (ValueError, TypeError):
            raise ValidationError(f"invalid value for {f.title}: {v!r}") from None
        if f.type in ("int", "float"):
            if (f.lo is not None and v < f.lo) or (f.hi is not None and v > f.hi):
                raise ValidationError(f"{f.title} must be between {f.lo} and {f.hi}")
        out[f.name] = v
    return out


def _upsert(db: Session, model, day: date, values: dict):
    row = db.query(model).filter_by(day=day).first()
    if row:
        for k, v in values.items():
            if v is not None:
                setattr(row, k, v)
        return row
    row = model(**values)
    db.add(row)
    return row


def _sleep_minutes(bed: str | None, wake: str | None) -> int | None:
    if not bed or not wake:
        return None
    bh, bm = map(int, bed.split(":"))
    wh, wm = map(int, wake.split(":"))
    return ((wh * 60 + wm) - (bh * 60 + bm)) % (24 * 60)


def create(db: Session, kind: str, raw: dict, source: str = "manual"):
    d = parse(kind, raw)
    session_keys = {"day", "start_hour", "minutes", "method", "material", "breaks", "focus", "fatigue",
                    "distractions", "retention_score", "notes"}
    if kind in ("german_session", "law_session"):
        base = {k: d[k] for k in session_keys}
        s = LearningSession(domain="german" if kind == "german_session" else "law", source=source, **base)
        if kind == "german_session":
            s.language = LanguageSession(skill=d["skill"], mode=d["mode"] or "active", accuracy=d["accuracy"])
        else:
            s.law = LawSession(area=d["area"].lower(), activity=d["activity"] or "reading")
        db.add(s)
        obj = s
    elif kind == "test":
        obj = Test(source=source, domain=d["domain"], area=(d["area"] or "").lower(), title=d["title"] or "",
                   day=d["day"], score=d["score"], max_score=d["max_score"] or 100, duration_min=d["duration_min"])
        if obj.score > obj.max_score:
            raise ValidationError("score cannot exceed max score")
        db.add(obj)
    elif kind == "questions":
        if d["correct"] > d["total"]:
            raise ValidationError("correct cannot exceed total")
        objs = [Question(source=source, domain=d["domain"], area=d["area"].lower(), topic=d["topic"] or "",
                         day=d["day"], correct=i < d["correct"], time_sec=d["avg_time_sec"])
                for i in range(d["total"])]
        db.add_all(objs)
        obj = objs[0]
    elif kind == "error":
        cat = d["category"].strip()
        obj = db.query(Error).filter_by(domain=d["domain"], category=cat, status="open").first()
        if obj:
            obj.occurrences += 1
            obj.last_seen = max(obj.last_seen, d["day"])
        else:
            obj = Error(source=source, domain=d["domain"], area=(d["area"] or "").lower(), category=cat,
                        description=d["description"], last_seen=d["day"])
            db.add(obj)
    elif kind == "sleep":
        dur = d["duration_min"] or _sleep_minutes(d["bed_time"], d["wake_time"])
        if not dur:
            raise ValidationError("give duration or both bed and wake time")
        obj = _upsert(db, Sleep, d["day"], {"day": d["day"], "bed_time": d["bed_time"] or "",
                                            "wake_time": d["wake_time"] or "", "duration_min": dur,
                                            "quality": d["quality"], "source": source})
    elif kind in ("recovery", "movement", "screen_time"):
        model = {"recovery": Recovery, "movement": Movement, "screen_time": ScreenTime}[kind]
        obj = _upsert(db, model, d["day"], {**d, "source": source})
    else:
        model = {"energy": Energy, "workout": Workout, "deep_work": DeepWork}[kind]
        obj = model(source=source, **d)
        db.add(obj)
    db.flush()
    publish(db, "data.logged", kind=kind, id=obj.id)
    audit(db, "user" if source == "manual" else source, "log.create", kind, id=obj.id)
    return obj


def import_csv(db: Session, kind: str, text: str) -> dict:
    """CSV with a header row using the spec field names. Each row is validated independently."""
    reader = csv.DictReader(io.StringIO(text))
    ok, errors = 0, []
    for i, row in enumerate(reader, start=2):
        try:
            with db.begin_nested():
                create(db, kind, row, source="import:csv")
            ok += 1
        except ValidationError as exc:
            errors.append(f"line {i}: {exc}")
        if len(errors) > 50:
            errors.append("too many errors, stopped")
            break
    db.commit()
    return {"imported": ok, "errors": errors}
