"""APEX cycle helpers for Claude Code sessions (stdlib only).

  prepare DUMP_DIR STATE_JSON          artifact-db dump  -> bridge input
  writes  DUMP_DIR BRIDGE_OUT OUT_DIR   bridge output     -> create-only write files + writes.json

Create-only by design: every write is a NEW document (projections/<run>, runs/<run>, inbox/<key>@<day>),
so the session never overwrites something the user changed. Personal data stays in /tmp; never commit it.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _docs(dump: Path, collection: str) -> dict[str, dict]:
    d = dump / collection
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))} if d.is_dir() else {}


def prepare(dump: str, out: str) -> None:
    dump_p = Path(dump)
    events = []
    for day_id, doc in _docs(dump_p, "events").items():
        for e in doc.get("entries", []):
            events.append({**e, "_doc": day_id})
    plan = []
    for day_id, doc in _docs(dump_p, "plan").items():
        for it in doc.get("items", []):
            plan.append({**it, "day": doc.get("day", day_id)})
    state = {
        "today": datetime.now().date().isoformat(),
        "events": events,
        "goals": [{"id": k, **v} for k, v in _docs(dump_p, "goals").items()],
        "skills": list(_docs(dump_p, "skills").values()),
        "plan": plan,
        "opportunities": list(_docs(dump_p, "opportunities").values()),
    }
    Path(out).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"events": len(events), "goals": len(state["goals"]), "plan_items": len(plan)}))


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.~:@+-]", "-", s)[:150]


def writes(dump: str, bridge_out: str, out_dir: str) -> None:
    dump_p, out_p = Path(dump), Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    proj = json.loads(Path(bridge_out).read_text(encoding="utf-8"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = proj["day"]
    existing_inbox = set(_docs(dump_p, "inbox"))
    plan: list[dict] = []

    def add(collection: str, doc_id: str, data: dict) -> None:
        f = out_p / f"{collection}__{doc_id}.json"
        f.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        plan.append({"op": "set", "collection": collection, "doc_id": doc_id, "file_path": str(f)})

    add("projections", run_id, {**proj, "run_id": run_id})
    for s in proj.get("signals", []):
        if s["class"] == "FYI":
            continue  # digest only; visible in the projection
        doc_id = _slug(f"{s['key']}@{day}")
        if doc_id in existing_inbox:
            continue
        add("inbox", doc_id, {"title": s["title"], "so_what": s["so_what"], "class": s["class"],
                              "priority": s["priority"], "kind": s["inbox_kind"], "deadline": s["deadline"],
                              "evidence": s["evidence"], "status": "unread", "source": "engine",
                              "created_at": datetime.now(timezone.utc).isoformat()})
    batches = [plan[i:i + 50] for i in range(0, len(plan), 50)]
    (out_p / "writes.json").write_text(json.dumps({"run_id": run_id, "batches": batches}, ensure_ascii=False),
                                       encoding="utf-8")
    print(json.dumps({"run_id": run_id, "documents": len(plan), "batches": len(batches)}))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"prepare": prepare, "writes": writes}[cmd](*args)
