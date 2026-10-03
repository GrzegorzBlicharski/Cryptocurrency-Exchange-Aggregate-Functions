"""Radar sources: RSS/Atom feeds (heuristic scoring) and LLM web research (cited only).
Everything is stored with source + retrieval time; the EV gate decides what is shown."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..engines import radar
from ..models import Goal, ResearchItem, Skill
from ..untrusted import sanitize
from . import feeds, http, llm, store


def _keywords(db: Session, extra: list[str]) -> list[str]:
    words = {w.lower() for w in extra if w.strip()}
    for g in db.query(Goal).filter_by(status="active").all():
        words |= {w.lower() for w in g.title.split() if len(w) > 4}
    words |= {s.name.lower() for s in db.query(Skill).all()}
    return sorted(words)


def heuristic_score(text: str, keywords: list[str]) -> dict:
    hits = sum(1 for k in keywords if k in text.lower())
    rel = max(1, min(5, 1 + hits))
    # Unknown evidence/impact stay neutral-low: a feed item must earn its way past the gate.
    return {"relevance": rel, "impact": 3, "evidence": 2, "actionability": 3, "time_cost_h": 1.0, "money_cost": 0.0}


def _store(db: Session, it: dict, category: str, source: str) -> bool:
    url = it["url"][:500]
    if db.query(ResearchItem).filter_by(url=url).first():
        return False
    summary, flags = sanitize(it.get("summary", ""))
    clamp = lambda v: max(1, min(int(v), 5))  # noqa: E731
    sc = {k: clamp(it[k]) for k in ("relevance", "impact", "evidence", "actionability")}
    t, m = max(0.0, float(it["time_cost_h"])), max(0.0, float(it["money_cost"]))
    db.add(ResearchItem(category=category if category in radar.CATEGORIES else "TOOLS",
                        title=sanitize(it["title"])[0][:300], url=url, summary=summary[:2000],
                        time_cost_h=t, money_cost=m, injection_flags=flags,
                        expected_value=radar.expected_value(sc["relevance"], sc["impact"], sc["evidence"],
                                                            sc["actionability"], t, m),
                        source=source, retrieved_at=datetime.now(timezone.utc), **sc))
    return True


def sync(db: Session) -> dict:
    cfg = store.get(db, "radar")
    kws = _keywords(db, cfg.get("keywords", []))
    added, errors = 0, []
    for f in cfg.get("feeds", []):
        url, category = (f["url"], f.get("category", "TOOLS")) if isinstance(f, dict) else (f, "TOOLS")
        try:
            for e in feeds.parse(http.get(url).content)[:30]:
                it = {"title": e.title, "url": e.url, "summary": e.summary,
                      **heuristic_score(f"{e.title} {e.summary}", kws)}
                added += _store(db, it, category, "integration:feed")
        except (http.FetchError, ValueError) as exc:
            errors.append(f"{url[:60]}: {exc}")
    if cfg.get("web_queries") and llm.available():
        goals = "; ".join(g.title for g in db.query(Goal).filter_by(status="active").all())
        for q in cfg["web_queries"][:5]:
            try:
                for it in llm.web_research(db, q, f"Goals: {goals}"):
                    added += _store(db, it, str(it.get("category", "TOOLS")).upper(), "integration:web_search")
            except llm.LLMUnavailable as exc:
                errors.append(f"web '{q[:30]}': {exc}")
                break
    store.mark(db, "radar", f"{added} new" + (f"; errors: {'; '.join(errors)[:200]}" if errors else ""))
    db.commit()
    return {"added": added, "errors": errors}
