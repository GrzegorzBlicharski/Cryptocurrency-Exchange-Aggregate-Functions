"""Alembic environment. URL comes from APEX settings; batch mode keeps SQLite ALTERs working."""
from alembic import context
from sqlalchemy import Text, create_engine

from apex.config import get_settings
from apex.crypto import EncryptedText
from apex.models import Base

config = context.config
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    if type_ == "type" and isinstance(obj, EncryptedText):
        return "sa.Text()"  # stored as text; encryption happens in the ORM layer
    return False


def run() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        engine = create_engine(config.get_main_option("sqlalchemy.url") or get_settings().database_url)
        with engine.connect() as conn:
            _run(conn)
            conn.commit()
    else:
        _run(connection)


def _run(conn) -> None:
    context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True,
                      render_item=render_item, compare_type=False)
    with context.begin_transaction():
        context.run_migrations()


run()
assert Text  # keep import for generated scripts' reference
