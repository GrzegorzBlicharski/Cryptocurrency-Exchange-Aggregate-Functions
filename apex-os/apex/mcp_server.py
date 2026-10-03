"""Minimal MCP server (JSON-RPC 2.0 over stdio, newline-delimited) — no SDK needed.

Exposes APEX to AI clients (Claude, ChatGPT/Codex, IDE agents) with least privilege:
read tools + observation logging (autonomy level 0). There is deliberately NO tool to
approve actions, change autonomy, delete data or read raw memory — those stay human-only.
Run: python -m apex mcp
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import IO

from . import logbook, memory, reviews
from .agents.orchestrator import Orchestrator
from .audit import audit
from .config import get_settings
from .db import session_scope
from .models import InboxItem

PROTOCOL = "2025-06-18"


def _today():
    return datetime.now(get_settings().tz).date()


def t_status(db, args):
    plan = Orchestrator().plan(db, _today())
    return {"apex_score": plan.apex_score, "bottleneck": plan.bottleneck,
            "sustainability": {"index": plan.sustainability.index, "band": plan.sustainability.band},
            "capacity_left_min": plan.capacity_left_min,
            "next_best_actions": [{"title": a.action.title, "minutes": a.action.minutes, "score": a.score,
                                   "why": a.action.why.reasoning} for a in plan.admitted],
            "domains": {k: {"score": r.status.score, "headline": r.status.headline, "so_what": r.status.so_what}
                        for k, r in plan.reports.items()}}


def t_brief(db, args):
    return reviews.morning_brief(db, _today(), store=False)


def t_inbox(db, args):
    items = (db.query(InboxItem).filter(InboxItem.status.in_(("unread", "read")))
             .order_by(InboxItem.priority.desc()).limit(int(args.get("limit", 20))).all())
    return [{"kind": i.kind, "title": i.title, "so_what": i.so_what, "priority": i.priority} for i in items]


def t_law_graph(db, args):
    return Orchestrator().plan(db, _today()).reports["law"].extras.get("knowledge_graph", {})


def t_twin(db, args):
    return {q: [{"statement": r.content, "epistemic": r.epistemic, "confidence": r.confidence} for r in recs[:5]]
            for q, recs in memory.digital_twin(db).items()}


def t_log(db, args):
    kind = args.get("kind", "")
    data = args.get("data") or {}
    obj = logbook.create(db, kind, data, source="mcp")
    audit(db, "mcp", "mcp.log", kind, id=obj.id)
    return {"ok": True, "kind": kind, "id": obj.id}


TOOLS = {
    "apex_status": (t_status, "Current APEX score, sustainability, next best actions and domain status.", {}),
    "apex_brief": (t_brief, "Today's morning brief (priority, top 3, learning target, deadline, avoid).", {}),
    "apex_inbox": (t_inbox, "Open inbox items ordered by priority.",
                   {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    "apex_law_knowledge_graph": (t_law_graph, "Law areas with STRONG/DEVELOPING/WEAK/CRITICAL GAP status.", {}),
    "apex_digital_twin": (t_twin, "Personal operating model findings with epistemic labels.", {}),
    "apex_log": (t_log, "Log an observation (e.g. a study session, sleep, steps). Kinds: "
                 + ", ".join(logbook.SPECS) + ". Fields as in GET /api/v1/log/kinds.",
                 {"kind": {"type": "string", "enum": list(logbook.SPECS)}, "data": {"type": "object"}}),
}


def handle(msg: dict) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "apex-os", "version": "0.2.0"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": {
            "type": "object", "properties": props, **({"required": ["kind", "data"]} if n == "apex_log" else {})}}
            for n, (_, d, props) in TOOLS.items()]})
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            with session_scope() as db:
                out = TOOLS[name][0](db, params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": json.dumps(out, default=str, ensure_ascii=False)}],
                       "isError": False})
        except (logbook.ValidationError, ValueError, KeyError) as exc:
            return ok({"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(msg) if isinstance(msg, dict) else None
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


