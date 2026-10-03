"""RSS 2.0 / Atom parsing (stdlib, no external entity resolution)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class FeedEntry:
    title: str
    url: str
    summary: str
    published: datetime | None
    uid: str


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse(xml_bytes: bytes) -> list[FeedEntry]:
    if b"<!ENTITY" in xml_bytes[:4000]:
        raise ValueError("feeds with DTD entities are rejected")  # billion-laughs / XXE guard
    root = ET.fromstring(xml_bytes)
    out: list[FeedEntry] = []
    for item in root.iter("item"):  # RSS
        link = (item.findtext("link") or "").strip()
        out.append(FeedEntry((item.findtext("title") or "").strip(), link,
                             (item.findtext("description") or "").strip(), _date(item.findtext("pubDate")),
                             (item.findtext("guid") or link).strip()))
    for e in root.iter(f"{_ATOM}entry"):  # Atom
        link_el = e.find(f"{_ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = e.find(f"{_ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        out.append(FeedEntry((e.findtext(f"{_ATOM}title") or "").strip(), link.strip(),
                             (e.findtext(f"{_ATOM}summary") or e.findtext(f"{_ATOM}content") or "").strip(),
                             _date(e.findtext(f"{_ATOM}updated") or e.findtext(f"{_ATOM}published")),
                             (e.findtext(f"{_ATOM}id") or link).strip()))
    return [x for x in out if x.title and re.match(r"https://", x.url)]
