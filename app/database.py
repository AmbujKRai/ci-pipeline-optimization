"""Database engine and session helpers (SQLAlchemy 2)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

IN_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:"}


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def make_engine(database_url: str) -> Engine:
    """Create an engine. SQLite gets foreign keys switched on for every connection."""
    if not database_url.startswith("sqlite"):
        return create_engine(database_url, pool_pre_ping=True)

    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if database_url in IN_MEMORY_URLS:
        # A single shared connection, so every session sees the same in-memory database.
        kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        # The sqlite3 driver ignores this PRAGMA inside a transaction, so switch
        # autocommit on while it runs (the attribute only exists on Python 3.12+).
        previous = getattr(dbapi_connection, "autocommit", None)
        if previous is not None:
            dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        if previous is not None:
            dbapi_connection.autocommit = previous

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency that yields a session and always closes it."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
