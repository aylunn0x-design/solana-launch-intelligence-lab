"""Health and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.models import DataSource

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """Liveness plus a real database round-trip.

    Returns HTTP 200 with `"status": "degraded"` when the database is
    unreachable, rather than pretending to be healthy.
    """
    settings = get_settings()
    db_ok = True
    db_error: str | None = None
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        db_ok = False
        db_error = f"{type(exc).__name__}: {exc}"

    sources: list[dict] = []
    if db_ok:
        try:
            for source in session.execute(select(DataSource)).scalars():
                sources.append(
                    {
                        "name": source.name,
                        "enabled": source.is_enabled,
                        "requires_api_key": source.requires_api_key,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            db_error = f"sources unreadable: {exc}"

    return {
        "status": "ok" if db_ok else "degraded",
        "environment": settings.environment,
        "database": {
            "reachable": db_ok,
            "engine": "sqlite" if settings.is_sqlite else "postgresql",
            "error": db_error,
        },
        "data_sources": sources,
        "holder_collection_enabled": settings.enable_holder_collection,
        "disclaimer": (
            "Research instrument. Associations from observational data; not financial "
            "advice and not a trading system."
        ),
    }
