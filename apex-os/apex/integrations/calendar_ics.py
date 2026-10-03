"""Calendar via iCalendar (ICS).

Read: the private ICS address of the user's calendar (Google: Settings → "Secret address in
iCal format"). Only busy time is used (reduces today's focus capacity); titles are encrypted.
Write: APEX never writes to the calendar. It publishes a read-only ICS feed of the plan
(/calendar/<token>.ics) which the user may subscribe to — a reversible, user-controlled step.
"""
from __future__ import annotations

import hmac
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from ..audit import publish
from ..config import get_settings
from ..models import CalendarEvent, PlanItem
from . import http, store

WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_dt(value: str, params: dict, tz: ZoneInfo) -> tuple[datetime, bool]:
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=tz), True
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(tz), False
    src = tz
    if "TZID" in params:
        try:
            src = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            src = tz
    return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=src).astimezone(tz), False


def parse_events(text: str, tz: ZoneInfo) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": []}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur and cur.get("status") != "CANCELLED" and cur.get("transp") != "TRANSPARENT":
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *ps = head.split(";")
            params = dict(p.split("=", 1) for p in ps if "=" in p)
            if name == "DTSTART":
                cur["start"], cur["all_day"] = _parse_dt(value, params, tz)
            elif name == "DTEND":
                cur["end"], _ = _parse_dt(value, params, tz)
            elif name == "UID":
                cur["uid"] = value[:255]
            elif name == "SUMMARY":
                cur["title"] = value.replace("\\,", ",").replace("\\n", " ")[:200]
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                for v in value.split(","):
                    cur["exdates"].append(_parse_dt(v, params, tz)[0].date())
            elif name == "STATUS":
                cur["status"] = value
            elif name == "TRANSP":
                cur["transp"] = value
    for e in events:
        e.setdefault("end", e["start"] + (timedelta(days=1) if e["all_day"] else timedelta(hours=1)))
        e.setdefault("uid", f"{e['start'].isoformat()}-{e.get('title', '')[:20]}")
    return events


def occurrences(ev: dict, start: date, end: date) -> list[datetime]:
    """Expand DAILY/WEEKLY RRULEs (INTERVAL, COUNT, UNTIL, BYDAY) inside [start, end]."""
    first: datetime = ev["start"]
    rule = ev.get("rrule")
    if not rule:
        return [first] if start <= first.date() <= end else []
    freq = rule.get("FREQ")
    if freq not in ("DAILY", "WEEKLY"):
        return [first] if start <= first.date() <= end else []
    interval = int(rule.get("INTERVAL", 1))
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = datetime.strptime(rule["UNTIL"][:8], "%Y%m%d").date() if "UNTIL" in rule else None
    byday = [WEEKDAYS.index(d[-2:]) for d in rule.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS]
    if freq == "WEEKLY" and not byday:
        byday = [first.weekday()]
    out, n, d = [], 0, first.date()
    limit = min(end, until) if until else end
    while d <= limit:
        if freq == "DAILY":
            ok = (d - first.date()).days % interval == 0
        else:
            week_idx = ((d - first.date()).days + first.weekday() - d.weekday()) // 7
            ok = d.weekday() in byday and week_idx % interval == 0
        if ok:
            n += 1
            if count is not None and n > count:
                break
            if d >= start and d not in ev["exdates"]:
                out.append(first.replace(year=d.year, month=d.month, day=d.day))
        d += timedelta(days=1)
    return out


def sync(db: Session, today: date | None = None) -> dict:
    s = get_settings()
    if not s.ics_url:
        return {"status": "not configured"}
    tz = s.tz
    today = today or datetime.now(tz).date()
    window_end = today + timedelta(days=7)
    try:
        text = http.get(s.ics_url).text
        events = parse_events(text, tz)
    except (http.FetchError, ValueError) as exc:
        store.mark(db, "calendar", f"error: {exc}")
        db.commit()
        return {"error": str(exc)}
    db.query(CalendarEvent).filter(CalendarEvent.source == "integration:ics", CalendarEvent.day >= today,
                                   CalendarEvent.day <= window_end).delete()
    n = 0
    for ev in events:
        dur = ev["end"] - ev["start"]
        for occ in occurrences(ev, today, window_end):
            fin = occ + dur
            db.add(CalendarEvent(uid=ev["uid"], day=occ.date(), all_day=ev["all_day"],
                                 start_min=0 if ev["all_day"] else occ.hour * 60 + occ.minute,
                                 end_min=1440 if ev["all_day"] or fin.date() > occ.date() else fin.hour * 60 + fin.minute,
                                 title=ev.get("title", ""), source="integration:ics"))
            n += 1
    store.mark(db, "calendar", f"{n} events in next 7 days")
    publish(db, "calendar.synced", events=n)
    db.commit()
    return {"events": n}


def busy_minutes(events: list[CalendarEvent], day_start: int = 7 * 60, day_end: int = 22 * 60) -> int:
    """Union of timed events inside waking hours (all-day events don't block time)."""
    spans = sorted((max(e.start_min, day_start), min(e.end_min, day_end)) for e in events if not e.all_day)
    total, cur_s, cur_e = 0, None, None
    for a, b in spans:
        if b <= a:
            continue
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = a, b
        else:
            cur_e = max(cur_e, b)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


# ------------------------------------------------------------------ plan feed (read-only)
def token_ok(token: str) -> bool:
    expected = get_settings().calendar_feed_token
    return bool(expected) and len(expected) >= 24 and hmac.compare_digest(token, expected)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\;").replace("\n", "\\n")


def schedule(items: list[PlanItem], busy: list[CalendarEvent], start_min: int = 9 * 60) -> list[tuple[PlanItem, int]]:
    """Place plan items sequentially from start_min, skipping busy blocks, 15-min breaks."""
    blocks = sorted((e.start_min, e.end_min) for e in busy if not e.all_day)
    out, t = [], start_min
    for it in items:
        dur = max(15, it.planned_min or 15)
        moved = True
        while moved:
            moved = False
            for a, b in blocks:
                if t < b and t + dur > a:
                    t, moved = b, True
        if t + dur > 22 * 60:
            break
        out.append((it, t))
        t += dur + 15
    return out


def plan_feed(db: Session, today: date) -> str:
    tz = get_settings().tz
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//APEX OS//plan//EN", "X-WR-CALNAME:APEX plan",
             "CALSCALE:GREGORIAN"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for i in range(0, 8):
        d = today + timedelta(days=i)
        items = db.query(PlanItem).filter(PlanItem.day == d, PlanItem.status == "planned").order_by(PlanItem.id).all()
        busy = db.query(CalendarEvent).filter_by(day=d).all()
        for it, t in schedule(items, busy):
            st = datetime(d.year, d.month, d.day, t // 60, t % 60, tzinfo=tz).astimezone(timezone.utc)
            en = st + timedelta(minutes=max(15, it.planned_min or 15))
            lines += ["BEGIN:VEVENT", f"UID:apex-plan-{it.id}@apex", f"DTSTAMP:{stamp}",
                      f"DTSTART:{st.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{en.strftime('%Y%m%dT%H%M%SZ')}",
                      f"SUMMARY:{_esc('APEX: ' + it.title)}", f"CATEGORIES:{_esc(it.domain)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
