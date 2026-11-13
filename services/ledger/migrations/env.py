"""Alembic environment: uses the ledger's own engine and models, so URL handling (Secret Manager
password, SQLite paths) is identical to the running service."""
import os
import sys

from alembic import context

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db import Base, engine  # noqa: E402

target_metadata = Base.metadata


def run() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:  # called from init_db(), which already holds a connection and a lock
        _configure(connection)
        return
    with engine.connect() as conn:
        _configure(conn)
        conn.commit()


def _configure(conn) -> None:
    context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=conn.dialect.name == "sqlite")  # SQLite can't ALTER most things in place
    with context.begin_transaction():
        context.run_migrations()


run()
