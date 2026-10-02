"""FastAPI application factory."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import api, db, scheduler, web
from .config import Settings, get_settings
from .models import User
from .security import NotAuthenticated


def create_app(settings: Settings | None = None, start_scheduler: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    db.init(settings)
    run_sched = settings.scheduler_enabled if start_scheduler is None else start_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(scheduler.loop()) if run_sched else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="APEX OS", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
    app.include_router(api.router)
    app.include_router(web.router)

    @app.exception_handler(NotAuthenticated)
    async def _unauth(request: Request, exc: NotAuthenticated):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        with db.session_scope() as s:
            has_user = s.query(User).count() > 0
        return RedirectResponse("/login" if has_user else "/setup", status_code=303)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        return resp

    return app
