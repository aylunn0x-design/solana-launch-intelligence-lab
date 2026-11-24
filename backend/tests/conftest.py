"""Shared test fixtures.

Tests run against a real SQLite database in a temp file (not an in-memory one),
so foreign keys, unique constraints and the connection pool behave as they do in
production. No test touches the network: provider tests mock the HTTP transport
with respx.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest

# Point the app at a throwaway database BEFORE app modules read settings.
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture(scope="session")
def _db_url(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("db") / "test.db"
    return f"sqlite:///{path}"


@pytest.fixture(scope="session", autouse=True)
def _configure_env(_db_url: str) -> None:
    os.environ["DATABASE_URL"] = _db_url
    from app.core.config import get_settings

    get_settings.cache_clear()


@pytest.fixture()
def session(_configure_env) -> Generator:
    """A session against freshly created tables, dropped after each test."""
    from app.core.db import SessionLocal, engine
    from app.models import Base

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def seeded_session(session):
    """A session with sources registered."""
    from app.providers.registry import sync_sources

    sync_sources(session)
    return session


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client(_configure_env):
    """FastAPI test client."""
    from fastapi.testclient import TestClient

    from app.core.db import engine
    from app.main import app
    from app.models import Base

    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)


def make_pool_with_history(
    session,
    *,
    mint: str,
    pool_address: str,
    created_at: datetime,
    hours: int = 48,
    volume_per_hour: float = 100.0,
    liquidity: float = 5000.0,
    dies_after_hours: int | None = None,
    discovery=None,
    dex: str = "pumpswap",
    price_start: float = 1.0,
):
    """Build a pool with deterministic candle history, for feature/survival tests."""
    from app.models import (
        DiscoveryMethod,
        MarketObservation,
        OhlcvCandle,
        Pool,
        Timeframe,
        Token,
    )
    from app.providers.registry import get_source_id

    source_id = get_source_id(session, "geckoterminal")
    token = Token(mint=mint, symbol=mint[:4], name=f"token {mint[:4]}", first_seen_at=created_at)
    session.add(token)
    session.flush()

    pool = Pool(
        pool_address=pool_address,
        token_id=token.id,
        network="solana",
        dex=dex,
        pool_name=f"{mint[:4]} / SOL",
        pool_created_at=created_at,
        discovery_method=discovery or DiscoveryMethod.NEW_POOLS_FEED,
        discovered_at=created_at,
        source_id=source_id,
    )
    session.add(pool)
    session.flush()

    session.add(
        MarketObservation(
            pool_id=pool.id,
            source_id=source_id,
            observed_at=created_at,
            liquidity_usd=liquidity,
            fdv_usd=liquidity * 4,
            price_usd=price_start,
            volume_usd_h24=volume_per_hour,
            txns_h24=10,
            unique_traders_h24=7,
            missing_field_count=0,
        )
    )

    for hour in range(hours):
        ts = created_at + timedelta(hours=hour)
        active = dies_after_hours is None or hour < dies_after_hours
        price = price_start * (1.0 + 0.01 * hour)
        session.add(
            OhlcvCandle(
                pool_id=pool.id,
                source_id=source_id,
                timeframe=Timeframe.HOUR,
                ts=ts,
                open=price,
                high=price * 1.05,
                low=price * 0.95,
                close=price,
                volume_usd=volume_per_hour if active else 0.0,
            )
        )
    session.commit()
    return pool
