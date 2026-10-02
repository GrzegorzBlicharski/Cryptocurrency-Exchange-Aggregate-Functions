"""Field-level encryption for sensitive free-text columns.

The key comes from APEX_DATA_KEY or a key file (mode 0600) in the data dir, so the
SQLite file and its backups do not contain readable notes on their own.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

_fernet: Fernet | None = None


def configure(key: str | None, data_dir: Path) -> None:
    global _fernet
    if not key:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file = data_dir / "apex.key"
        if key_file.exists():
            key = key_file.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(key)
    _fernet = Fernet(key.encode() if isinstance(key, str) else key)


def _get() -> Fernet:
    if _fernet is None:
        raise RuntimeError("encryption not configured; call apex.crypto.configure() first")
    return _fernet


def encrypt(value: str) -> str:
    return _get().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _get().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("cannot decrypt field: wrong APEX_DATA_KEY?") from exc


class EncryptedText(TypeDecorator):
    """Text column transparently encrypted at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        return decrypt(value)
