"""Database engine and session management.

Supports SQLite (zero-setup local mode) and PostgreSQL (Docker / production)
behind the same SQLAlchemy API.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _build_engine() -> Engine:
    settings = get_settings()
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if settings.is_sqlite:
        # check_same_thread=False: FastAPI serves requests from a threadpool.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10

    engine = create_engine(settings.database_url, **kwargs)

    if settings.is_sqlite:
        # SQLite does not enforce foreign keys unless asked. Referential
        # integrity is part of the schema contract, so turn it on. WAL improves
        # concurrent read behaviour while ingestion writes.
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
