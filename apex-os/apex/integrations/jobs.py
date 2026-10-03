"""Job sources → CareerOpportunity rows (source + retrieval time always stored).

Sources: user-configured RSS/Atom feeds, and the public Arbeitnow job-board API
(German-market jobs, no key). Requirements come from the LLM when configured,
otherwise from a transparent keyword heuristic. The Career agent's high-signal
filter decides what reaches you; ingestion is capped per sync.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..audit import audit, publish
from ..models import CareerOpportunity, Skill
from ..untrusted import sanitize
from . import feeds, http, llm, store

ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
CEFR = {"a2": 1, "b1": 2, "b2": 3, "c1": 4, "c2": 5}
LANG_WORDS = {"german": ("german", "deutsch"), "english": ("english", "englisch"), "french": ("french", "französisch")}


def heuristic_requirements(text: str, skills: list[str]) -> list[dict]:
    """Known skills mentioned in the text; CEFR levels next to a language set its level."""
    low = text.lower()
    reqs = []
    for name in skills:
        n = name.lower()
        words = LANG_WORDS.get(n, (n,))
        hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", low)), None)
        if not hit:
            continue
        level = 3
        m = re.search(rf"\b{re.escape(hit)}\w*\W{{0,20}}\(?\b([abc][12])\b|\b([abc][12])\b\W{{0,20}}{re.escape(hit)}", low)
        if m:
            level = CEFR[(m.group(1) or m.group(2))]
        elif re.search(rf"(fluent|fließend|verhandlungssicher)\W{{0,20}}{re.escape(hit)}|{re.escape(hit)}\W{{0,20}}(fluent|fließend|verhandlungssicher)", low):
            level = 4
        opt = r"(nice to have|a plus|ein plus|wünschenswert|von vorteil|preferred|optional)"
        optional = bool(re.search(rf"{opt}[^.;\n]{{0,60}}\b{re.escape(hit)}|\b{re.escape(hit)}\b[^.;,\n]{{0,25}}{opt}", low))
        reqs.append({"skill": name, "level": level, "required": not optional})
    return reqs


def _matches(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(k.lower() in low for k in keywords if k.strip())


def _ingest(db: Session, items: list[dict], source: str, cfg: dict, use_llm: bool) -> int:
    skills = [s.name for s in db.query(Skill).all()]
    added = 0
    for it in items:
        if added >= int(cfg.get("max_per_sync", 25)):
            break
        url = it["url"][:500]
        if not url or db.query(CareerOpportunity).filter_by(url=url).first():
            continue
        desc, flags = sanitize(it.get("description", ""))
        if not _matches(f"{it['title']} {desc}", cfg.get("keywords", [])):
            continue
        reqs, how = [], "heuristic"
        if use_llm:
            try:
                reqs, how = llm.extract_requirements(db, desc[:12000], skills), "llm"
            except llm.LLMUnavailable:
                use_llm = False
        if not reqs:
            reqs = heuristic_requirements(f"{it['title']} {desc}", skills)
        db.add(CareerOpportunity(
            title=sanitize(it["title"])[0][:200], organization=sanitize(it.get("organization", ""))[0][:200],
            location=it.get("location", "")[:128], url=url, description=desc, injection_flags=flags,
            requirements=reqs, source=f"integration:{source}", retrieved_at=datetime.now(timezone.utc),
            strategic_fit=3, salary_source="", salary_text=""))
        added += 1
        audit(db, f"integration:{source}", "career.ingest", url[:120], requirements=how)
    return added


def fetch_feed(url: str) -> list[dict]:
    entries = feeds.parse(http.get(url).content)
    return [{"title": e.title, "url": e.url, "description": e.summary, "organization": ""} for e in entries]


def fetch_arbeitnow(pages: int = 2) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        data = http.get(f"{ARBEITNOW}?page={page}").json()
        for j in data.get("data", []):
            out.append({"title": j.get("title", ""), "url": j.get("url", ""), "description": j.get("description", ""),
                        "organization": j.get("company_name", ""),
                        "location": ("remote" if j.get("remote") else "") + " " + j.get("location", "")})
    return out


def sync(db: Session) -> dict:
    cfg = store.get(db, "job_sources")
    use_llm = llm.available()
    result, errors = {}, []
    sources = [(f"feed:{u[:60]}", lambda u=u: fetch_feed(u)) for u in cfg.get("feeds", [])]
    if cfg.get("arbeitnow"):
        sources.append(("arbeitnow", fetch_arbeitnow))
    for name, fn in sources:
        try:
            result[name] = _ingest(db, fn(), name.split(":")[0], cfg, use_llm)
        except (http.FetchError, ValueError, KeyError) as exc:  # one bad source never blocks the others
            errors.append(f"{name}: {exc}")
    total = sum(result.values())
    store.mark(db, "job_sources", f"{total} new" + (f"; errors: {'; '.join(errors)[:200]}" if errors else ""))
    if total:
        publish(db, "career.synced", added=total)
    db.commit()
    return {"added": result, "errors": errors}
