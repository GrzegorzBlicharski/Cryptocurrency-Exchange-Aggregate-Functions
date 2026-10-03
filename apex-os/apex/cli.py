"""Command line: `python -m apex <command>`."""
from __future__ import annotations

import argparse
import getpass
import json
from datetime import datetime
from pathlib import Path

from . import db, demo, reviews, scheduler, security
from .agents.orchestrator import Orchestrator
from .config import get_settings
from .dataops import backup_sqlite


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="apex", description="APEX OS - Personal Operating Agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="run the web app")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    cu = sub.add_parser("create-user", help="create the single owner account")
    cu.add_argument("username")
    sub.add_parser("cycle", help="run the orchestrator cycle now and print the plan")
    sub.add_parser("brief", help="print today's morning brief")
    sub.add_parser("tick", help="run due scheduled jobs once")
    dm = sub.add_parser("demo", help="load or remove synthetic demo data")
    dm.add_argument("--remove", action="store_true")
    sub.add_parser("mcp", help="run the MCP server on stdio")
    sub.add_parser("sync", help="run all configured integration syncs now")
    mg = sub.add_parser("migrate", help="apply database migrations (alembic upgrade head)")
    mg.add_argument("--revision", default="head")
    bk = sub.add_parser("backup", help="online SQLite backup")
    bk.add_argument("--dest", default=None)
    a = p.parse_args(argv)

    s = get_settings()
    db.init(s)
    today = datetime.now(s.tz).date()
    if a.cmd == "serve":
        import uvicorn

        from .main import create_app

        uvicorn.run(create_app(s), host=a.host, port=a.port)
    elif a.cmd == "create-user":
        pw = getpass.getpass("password (min 10 chars): ")
        with db.session_scope() as d:
            security.create_user(d, a.username, pw)
        print("owner created")
    elif a.cmd == "cycle":
        with db.session_scope() as d:
            plan = Orchestrator().run_cycle(d, today, force=True)
            print(f"APEX score {plan.apex_score} | sustainability {plan.sustainability.band} "
                  f"({plan.sustainability.index}) | capacity {plan.capacity_min} min")
            for i, x in enumerate(plan.admitted, 1):
                print(f"{i}. [{x.score:5.1f}] {x.action.title} ({x.action.minutes} min) {x.note}")
            for x in plan.deferred:
                print(f"   deferred: {x.action.title} — {x.reason}")
    elif a.cmd == "brief":
        with db.session_scope() as d:
            print(json.dumps(reviews.morning_brief(d, today), indent=2, ensure_ascii=False))
    elif a.cmd == "tick":
        print(scheduler.tick())
    elif a.cmd == "demo":
        with db.session_scope() as d:
            if a.remove:
                print(f"removed {demo.remove(d)} demo rows")
            else:
                demo.seed(d, today)
                print("demo data loaded (source='demo'); remove with: apex demo --remove")
    elif a.cmd == "mcp":
        from .mcp_server import serve

        serve()
    elif a.cmd == "sync":
        from .integrations import calendar_ics, mail_imap, notify, radar_sync
        from .integrations import jobs as jobs_src

        for name, fn in [("jobs", jobs_src.sync), ("radar", radar_sync.sync), ("calendar", calendar_ics.sync),
                         ("mail", mail_imap.sync), ("notify", notify.deliver)]:
            with db.session_scope() as d:
                print(name, fn(d))
    elif a.cmd == "migrate":
        from .migrations_runner import upgrade

        upgrade(a.revision)
        print("database at", a.revision)
    elif a.cmd == "backup":
        url = s.database_url
        if not url.startswith("sqlite:///"):
            raise SystemExit("backup command supports file-based SQLite only")
        dest = backup_sqlite(Path(url.removeprefix("sqlite:///")), Path(a.dest) if a.dest else s.data_dir / "backups")
        print(f"backup written: {dest}")


if __name__ == "__main__":
    main()
