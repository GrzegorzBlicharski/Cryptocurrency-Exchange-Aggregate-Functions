"""Authentication for the single owner.

- scrypt password hashing (stdlib)
- random session tokens; only their SHA-256 is stored
- cookie: HttpOnly + SameSite=Strict; Bearer header also accepted for API clients
- unsafe methods with a cookie must come from the same origin (CSRF defense in depth)
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuthSession, User

COOKIE = "apex_session"
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        cand = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(cand.hex(), dk)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(db: Session, username: str, password: str, display_name: str = "") -> User:
    if db.query(User).count() > 0:
        raise ValueError("owner already exists; APEX is single-owner")
    user = User(username=username, password_hash=hash_password(password), display_name=display_name or username)
    db.add(user)
    db.commit()
    return user


def login(db: Session, username: str, password: str) -> str | None:
    user = db.query(User).filter_by(username=username).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=get_settings().session_days)
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(token), expires_at=expires))
    db.commit()
    return token


def logout(db: Session, token: str) -> None:
    db.query(AuthSession).filter_by(token_hash=_token_hash(token)).delete()
    db.commit()


def user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    s = db.query(AuthSession).filter_by(token_hash=_token_hash(token)).first()
    if not s:
        return None
    exp = s.expires_at if s.expires_at.tzinfo else s.expires_at.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        db.delete(s)
        db.commit()
        return None
    return db.get(User, s.user_id)


def _extract_token(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    return request.cookies.get(COOKIE), True


def _check_origin(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return  # SameSite=Strict already blocks cross-site cookie sends
    host = request.headers.get("host", "")
    if urlsplit(origin).netloc != host:
        raise HTTPException(status_code=403, detail="cross-origin request rejected")


class NotAuthenticated(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token, via_cookie = _extract_token(request)
    user = user_for_token(db, token)
    if not user:
        raise NotAuthenticated()
    if via_cookie:
        _check_origin(request)
    return user
