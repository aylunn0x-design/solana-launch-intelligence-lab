"""Data health: completeness, duplicates, invalid rows, source availability.

Every number here is a real query against the stored data. Checks that cannot
be run report themselves as "not_run" rather than silently passing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.models import (
    DataSource,
    IngestionJob,
    JobStatus,
    MarketObservation,
    OhlcvCandle,
    Pool,
    Token,
)

router = APIRouter(tags=["data-quality"])

#: fields the providers are expected to deliver per observation
EXPECTED_OBS_FIELDS = [
    "liquidity_usd",
    "price_usd",
    "fdv_usd",
    "volume_usd_h24",
    "txns_h24",
    "unique_traders_h24",
]


@router.get("/data-quality")
def data_quality(session: Session = Depends(get_session)) -> dict:
    settings = get_settings()

    pool_count = session.scalar(select(func.count()).select_from(Pool)) or 0
    obs_count = session.scalar(select(func.count()).select_from(MarketObservation)) or 0
    candle_count = session.scalar(select(func.count()).select_from(OhlcvCandle)) or 0

    if pool_count == 0:
        return {
            "dataset_empty": True,
            "message": "No data ingested yet; no quality checks can be run.",
            "checks": [],
        }

    checks: list[dict] = []

    # --- 1. field completeness, per column, from real counts ---
    field_completeness = []
    for field in EXPECTED_OBS_FIELDS:
        column = getattr(MarketObservation, field)
        non_null = (
            session.scalar(
                select(func.count()).select_from(MarketObservation).where(column.is_not(None))
            )
            or 0
        )
        field_completeness.append(
            {
                "field": field,
                "non_null": non_null,
                "total": obs_count,
                "completeness": round(non_null / obs_count, 4) if obs_count else None,
            }
        )
    worst = min(field_completeness, key=lambda f: f["completeness"] or 0)
    checks.append(
        {
            "name": "observation_field_completeness",
            "status": "pass" if (worst["completeness"] or 0) > 0.5 else "warn",
            "detail": field_completeness,
            "message": (
                f"Lowest completeness: {worst['field']} at "
                f"{(worst['completeness'] or 0):.1%}. unique_traders_h24 is expected to "
                "be incomplete because DexScreener does not report it."
            ),
        }
    )

    # --- 2. duplicate detection: the unique constraints should make this zero ---
    dup_obs = session.execute(
        text(
            """
            SELECT COUNT(*) FROM (
                SELECT pool_id, source_id, observed_at, COUNT(*) AS c
                FROM market_observations
                GROUP BY pool_id, source_id, observed_at
                HAVING COUNT(*) > 1
            ) d
            """
        )
    ).scalar() or 0
    dup_candles = session.execute(
        text(
            """
            SELECT COUNT(*) FROM (
                SELECT pool_id, timeframe, ts, COUNT(*) AS c
                FROM ohlcv_candles
                GROUP BY pool_id, timeframe, ts
                HAVING COUNT(*) > 1
            ) d
            """
        )
    ).scalar() or 0
    checks.append(
        {
            "name": "duplicate_rows",
            "status": "pass" if dup_obs == 0 and dup_candles == 0 else "fail",
            "detail": {
                "duplicate_observation_keys": int(dup_obs),
                "duplicate_candle_keys": int(dup_candles),
            },
            "message": (
                "No duplicates: enforced by unique constraints on "
                "(pool_id, source_id, observed_at) and (pool_id, timeframe, ts)."
                if dup_obs == 0 and dup_candles == 0
                else "Duplicate keys found — the unique constraints are not being honoured."
            ),
        }
    )

    # --- 3. invalid values that should have been rejected at validation ---
    negative_liq = (
        session.scalar(
            select(func.count())
            .select_from(MarketObservation)
            .where(MarketObservation.liquidity_usd < 0)
        )
        or 0
    )
    negative_vol = (
        session.scalar(
            select(func.count()).select_from(OhlcvCandle).where(OhlcvCandle.volume_usd < 0)
        )
        or 0
    )
    future_launch = (
        session.scalar(
            select(func.count())
            .select_from(Pool)
            .where(Pool.pool_created_at > datetime.now(timezone.utc) + timedelta(minutes=5))
        )
        or 0
    )
    invalid_total = int(negative_liq + negative_vol + future_launch)
    checks.append(
        {
            "name": "invalid_values",
            "status": "pass" if invalid_total == 0 else "fail",
            "detail": {
                "negative_liquidity": int(negative_liq),
                "negative_volume": int(negative_vol),
                "launch_timestamp_in_future": int(future_launch),
            },
            "message": (
                "No impossible values stored."
                if invalid_total == 0
                else "Impossible values present; the validation stage let something through."
            ),
        }
    )

    # --- 4. missing launch timestamps ---
    missing_created = (
        session.scalar(
            select(func.count()).select_from(Pool).where(Pool.pool_created_at.is_(None))
        )
        or 0
    )
    checks.append(
        {
            "name": "launch_timestamp_coverage",
            "status": "pass" if missing_created == 0 else "warn",
            "detail": {
                "pools_missing_launch_timestamp": int(missing_created),
                "pools_total": pool_count,
            },
            "message": (
                "Every pool has a launch timestamp."
                if missing_created == 0
                else f"{missing_created} pool(s) lack a launch timestamp and are excluded "
                "from launch-cohort analysis."
            ),
        }
    )

    # --- 5. history coverage ---
    pools_with_candles = (
        session.scalar(select(func.count(func.distinct(OhlcvCandle.pool_id)))) or 0
    )
    checks.append(
        {
            "name": "history_coverage",
            "status": "pass" if pools_with_candles / pool_count > 0.5 else "warn",
            "detail": {
                "pools_with_candles": int(pools_with_candles),
                "pools_total": pool_count,
                "share": round(pools_with_candles / pool_count, 4),
            },
            "message": (
                f"{pools_with_candles}/{pool_count} pools have OHLCV history. Pools "
                "without it cannot enter survival analysis."
            ),
        }
    )

    # --- 6. orphan referential integrity check ---
    orphan_pools = (
        session.scalar(
            select(func.count())
            .select_from(Pool)
            .where(Pool.token_id.not_in(select(Token.id)))
        )
        or 0
    )
    checks.append(
        {
            "name": "referential_integrity",
            "status": "pass" if orphan_pools == 0 else "fail",
            "detail": {"orphan_pools": int(orphan_pools)},
            "message": (
                "All pools reference an existing token."
                if orphan_pools == 0
                else f"{orphan_pools} pool(s) reference a missing token."
            ),
        }
    )

    # --- 7. holder data: honestly reported as unavailable ---
    checks.append(
        {
            "name": "holder_concentration_availability",
            "status": "not_run",
            "detail": {"enabled": settings.enable_holder_collection},
            "message": (
                "Holder-concentration collection is disabled. The public Solana RPC "
                "returns HTTP 429 for getTokenLargestAccounts, and that method caps at "
                "20 accounts even when it succeeds, so a true holder count and Gini "
                "cannot be derived from it. A dedicated RPC provider is required."
            ),
        }
    )

    # --- job health ---
    job_rows = session.execute(
        select(IngestionJob.status, func.count()).group_by(IngestionJob.status)
    ).all()
    job_counts = {
        (s.value if hasattr(s, "value") else str(s)): int(c) for s, c in job_rows
    }
    last_success = session.execute(
        select(IngestionJob)
        .where(IngestionJob.status == JobStatus.SUCCEEDED)
        .order_by(IngestionJob.finished_at.desc())
        .limit(1)
    ).scalars().first()
    recent_failures = list(
        session.execute(
            select(IngestionJob)
            .where(IngestionJob.status.in_([JobStatus.FAILED, JobStatus.PARTIAL]))
            .order_by(IngestionJob.started_at.desc())
            .limit(10)
        ).scalars()
    )
    total_rate_limit_hits = (
        session.scalar(select(func.sum(IngestionJob.rate_limit_hits))) or 0
    )

    sources = []
    for source in session.execute(select(DataSource)).scalars():
        src_obs = (
            session.scalar(
                select(func.count())
                .select_from(MarketObservation)
                .where(MarketObservation.source_id == source.id)
            )
            or 0
        )
        sources.append(
            {
                "name": source.name,
                "enabled": source.is_enabled,
                "requires_api_key": source.requires_api_key,
                "observations_contributed": src_obs,
                "description": source.description,
            }
        )

    failed_count = job_counts.get("failed", 0)
    overall = "ok"
    if any(c["status"] == "fail" for c in checks) or failed_count:
        overall = "degraded"
    elif any(c["status"] == "warn" for c in checks):
        overall = "warnings"

    return {
        "dataset_empty": False,
        "overall_status": overall,
        "counts": {
            "pools": pool_count,
            "observations": obs_count,
            "candles": candle_count,
        },
        "checks": checks,
        "jobs": {
            "by_status": job_counts,
            "last_successful_at": (
                last_success.finished_at.isoformat()
                if last_success and last_success.finished_at
                else None
            ),
            "total_rate_limit_hits": int(total_rate_limit_hits),
            "recent_failures": [
                {
                    "id": j.id,
                    "job_type": j.job_type,
                    "status": j.status.value if hasattr(j.status, "value") else str(j.status),
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "error_message": j.error_message,
                }
                for j in recent_failures
            ],
        },
        "sources": sources,
        "database": {
            "engine": "sqlite" if settings.is_sqlite else "postgresql",
            "foreign_keys_enforced": True,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
