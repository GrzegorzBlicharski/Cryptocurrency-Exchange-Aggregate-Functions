"""Push delivery for INTERRUPT inbox items only (the notification budget already limited them).

Targets: ntfy (self-hostable or ntfy.sh with a long random topic) and/or a generic webhook.
Payload is minimal: title + one-line "so what" — no health details, no notes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..models import InboxItem
from . import http, store


def configured() -> bool:
    s = get_settings()
    return bool(s.ntfy_url or s.webhook_url)


def deliver(db: Session) -> dict:
    s = get_settings()
    if not configured() or not store.get(db, "notifications").get("enabled", True):
        return {"sent": 0, "status": "not configured"}
    items = (db.query(InboxItem).filter(InboxItem.interrupt.is_(True), InboxItem.notified_at.is_(None),
                                        InboxItem.status == "unread").order_by(InboxItem.priority.desc()).all())
    sent, errors = 0, []
    for it in items:
        title = it.title[:120]
        body = (it.so_what or "")[:300]
        try:
            if s.ntfy_url:
                headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high" if it.priority >= 100 else "default",
                           "Tags": it.kind.lower()}
                if s.ntfy_token:
                    headers["Authorization"] = f"Bearer {s.ntfy_token}"
                http.post(s.ntfy_url, data=body.encode(), headers=headers, retries=1)
            if s.webhook_url:
                http.post(s.webhook_url, json={"title": title, "body": body, "kind": it.kind,
                                               "priority": it.priority}, retries=1)
        except http.FetchError as exc:
            errors.append(str(exc))
            break  # don't hammer a failing endpoint; next tick retries
        it.notified_at = datetime.now(timezone.utc)
        sent += 1
        audit(db, "system", "notify.sent", f"inbox:{it.id}")
    store.mark(db, "notifications", f"sent {sent}" + (f"; error: {errors[0][:150]}" if errors else ""))
    db.commit()
    return {"sent": sent, "errors": errors}
