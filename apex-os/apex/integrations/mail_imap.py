"""Gmail / any mailbox via IMAP — READ-ONLY, headers only.

Data minimization: only Subject/From/Date/Message-ID headers are fetched (mailbox opened
read-only, BODY.PEEK so nothing is marked read); only career-relevant messages are kept, and
only sender domain + encrypted subject are stored. APEX cannot send, delete or move mail.
"""
from __future__ import annotations

import imaplib
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from sqlalchemy.orm import Session

from ..audit import publish
from ..config import get_settings
from ..models import MailItem
from ..untrusted import sanitize
from . import store

CATEGORIES = [  # first match wins; keywords in EN / DE / PL
    ("offer", r"\b(job offer|offer letter|vertragsangebot|arbeitsvertrag|oferta pracy|propozycja zatrudnienia)\b"),
    ("interview", r"\b(interview|vorstellungsgespräch|kennenlerngespräch|rozmowa kwalifikacyjna|rozmowa rekrutacyjna)\b"),
    ("rejection", r"\b(absage|unfortunately|leider|niestety|not been successful)\b"),
    ("deadline", r"\b(deadline|frist|due date|termin|bis zum|do dnia)\b"),
    ("application", r"\b(application|bewerbung|aplikacja|zgłoszenie|candidature)\b"),
]

ImapFactory = Callable[[str], imaplib.IMAP4]
_factory: ImapFactory = lambda host: imaplib.IMAP4_SSL(host, timeout=30)  # noqa: E731


def set_factory(f: ImapFactory | None) -> None:
    global _factory
    _factory = f or (lambda host: imaplib.IMAP4_SSL(host, timeout=30))


def classify(subject: str) -> str | None:
    low = subject.lower()
    for cat, rx in CATEGORIES:
        if re.search(rx, low):
            return cat
    return None


def find_deadline(subject: str, today: date) -> date | None:
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", subject)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?\b", subject)
        if not m:
            return None
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
    try:
        dt = date(y, mo, d)
    except ValueError:
        return None
    return dt if dt >= today - timedelta(days=1) else None


def _dec(v: str | None) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except (ValueError, LookupError):
        return v or ""


def sync(db: Session, today: date | None = None) -> dict:
    s = get_settings()
    if not (s.imap_host and s.imap_user and s.imap_password):
        return {"status": "not configured"}
    today = today or datetime.now(s.tz).date()
    days = int(store.get(db, "mail").get("days", 7))
    kept = 0
    try:
        conn = _factory(s.imap_host)
        try:
            conn.login(s.imap_user, s.imap_password)
            conn.select(s.imap_folder, readonly=True)
            since = (today - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = conn.search(None, "SINCE", since)
            ids = (data[0] or b"").split()[-300:]
            for mid in ids:
                typ, msg = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])")
                raw = next((p[1] for p in msg if isinstance(p, tuple)), b"")
                h = message_from_bytes(raw)
                subject, flags = sanitize(_dec(h.get("Subject")))
                cat = classify(subject)
                msgid = (h.get("Message-ID") or f"{mid.decode()}@{s.imap_host}").strip()[:300]
                if not cat or db.query(MailItem).filter_by(message_id=msgid).first():
                    continue
                try:
                    received = parsedate_to_datetime(h.get("Date")).astimezone(timezone.utc)
                except (TypeError, ValueError):
                    received = datetime.now(timezone.utc)
                sender = parseaddr(_dec(h.get("From")))[1]
                db.add(MailItem(message_id=msgid, received_at=received, day=received.astimezone(s.tz).date(),
                                sender_domain=sender.split("@")[-1][:200], subject=subject[:300], category=cat,
                                deadline=find_deadline(subject, today), injection_flags=flags,
                                source="integration:imap"))
                kept += 1
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - best effort
                pass
    except (imaplib.IMAP4.error, OSError) as exc:
        store.mark(db, "mail", f"error: {exc}"[:300])
        db.commit()
        return {"error": str(exc)}
    store.mark(db, "mail", f"{kept} relevant messages")
    if kept:
        publish(db, "mail.synced", kept=kept)
    db.commit()
    return {"kept": kept}
