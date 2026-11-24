"""Overview metrics — all computed from stored rows.

If the database is empty, this returns `dataset_empty: true` with instructions,
so the UI can render a real empty state instead of zeros that look like findings.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import (
    DatasetVersion,
    DiscoveryMethod,
    IngestionJob,
    JobStatus,
    MarketObservation,
    OhlcvCandle,
    Pool,
    ResearchExperiment,
    Token,
)

router = APIRouter(tags=["overview"])

POPULATE_HINT = (
    "Populate the database with: "
    "`python -m app.cli init-db` then "
    "`python -m app.cli ingest --feed new --pages 10` and "
    "`python -m app.cli backfill --timeframe hour --limit-pools 50`."
)


@router.get("/overview")
def overview(session: Session = Depends(get_session)) -> dict:
    """Dataset size, coverage, freshness, distributions and data warnings."""
    token_count = session.scalar(select(func.count()).select_from(Token)) or 0
    pool_count = session.scalar(select(func.count()).select_from(Pool)) or 0
    observation_count = (
        session.scalar(select(func.count()).select_from(MarketObservation)) or 0
    )
    candle_count = session.scalar(select(func.count()).select_from(OhlcvCandle)) or 0

    if pool_count == 0:
        return {
            "dataset_empty": True,
            "message": "No pools have been ingested yet.",
            "how_to_populate": POPULATE_HINT,
            "counts": {
                "tokens": 0,
                "pools": 0,
                "market_observations": 0,
                "ohlcv_candles": 0,
            },
        }

    # --- launch date range ---
    first_launch, last_launch = session.execute(
        select(func.min(Pool.pool_created_at), func.max(Pool.pool_created_at))
    ).one()

    # --- cohort composition: the bias-relevant number ---
    cohort_rows = session.execute(
        select(Pool.discovery_method, func.count()).group_by(Pool.discovery_method)
    ).all()
    cohort_counts = {
        (m.value if hasattr(m, "value") else str(m)): int(c) for m, c in cohort_rows
    }
    inception = cohort_counts.get(DiscoveryMethod.NEW_POOLS_FEED.value, 0)

    # --- coverage: how many pools actually have usable history ---
    pools_with_candles = (
        session.scalar(select(func.count(func.distinct(OhlcvCandle.pool_id)))) or 0
    )
    pools_with_created_at = (
        session.scalar(
            select(func.count()).select_from(Pool).where(Pool.pool_created_at.is_not(None))
        )
        or 0
    )

    # --- ingestion freshness ---
    last_success = session.execute(
        select(IngestionJob)
        .where(IngestionJob.status == JobStatus.SUCCEEDED)
        .order_by(IngestionJob.finished_at.desc())
        .limit(1)
    ).scalars().first()

    failed_jobs = (
        session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .where(IngestionJob.status == JobStatus.FAILED)
        )
        or 0
    )

    # --- distributions, from real observations ---
    liquidity_stats = session.execute(
        select(
            func.min(MarketObservation.liquidity_usd),
            func.avg(MarketObservation.liquidity_usd),
            func.max(MarketObservation.liquidity_usd),
            func.count(MarketObservation.liquidity_usd),
        )
    ).one()

    activity_stats = session.execute(
        select(
            func.avg(MarketObservation.txns_h24),
            func.max(MarketObservation.txns_h24),
            func.count(MarketObservation.txns_h24),
        )
    ).one()

    unique_trader_coverage = (
        session.scalar(
            select(func.count())
            .select_from(MarketObservation)
            .where(MarketObservation.unique_traders_h24.is_not(None))
        )
        or 0
    )

    # --- research progress ---
    dataset_versions = session.execute(
        select(DatasetVersion).order_by(DatasetVersion.built_at.desc()).limit(5)
    ).scalars().all()
    experiment_count = (
        session.scalar(select(func.count()).select_from(ResearchExperiment)) or 0
    )
    recent_experiments = session.execute(
        select(ResearchExperiment).order_by(ResearchExperiment.created_at.desc()).limit(5)
    ).scalars().all()

    warnings = _data_quality_warnings(
        session,
        pool_count=pool_count,
        inception=inception,
        pools_with_candles=pools_with_candles,
        pools_with_created_at=pools_with_created_at,
        failed_jobs=failed_jobs,
        unique_trader_coverage=unique_trader_coverage,
        observation_count=observation_count,
    )

    return {
        "dataset_empty": False,
        "counts": {
            "tokens": token_count,
            "pools": pool_count,
            "market_observations": observation_count,
            "ohlcv_candles": candle_count,
            "dataset_versions": len(dataset_versions),
            "experiments": experiment_count,
        },
        "launch_date_range": {
            "earliest": first_launch.isoformat() if first_launch else None,
            "latest": last_launch.isoformat() if last_launch else None,
        },
        "cohort_composition": {
            "counts": cohort_counts,
            "inception_cohort_pools": inception,
            "inception_cohort_share": round(inception / pool_count, 4) if pool_count else None,
            "note": (
                "Only the inception cohort (discovered from the new-pools feed) is free "
                "of survivorship bias. Pools from 'top'/'trending' listings appear "
                "because they are currently notable."
            ),
        },
        "coverage": {
            "pools_with_history": pools_with_candles,
            "pools_with_history_share": round(pools_with_candles / pool_count, 4),
            "pools_with_launch_timestamp": pools_with_created_at,
            "pools_with_launch_timestamp_share": round(pools_with_created_at / pool_count, 4),
            "observations_with_unique_traders": unique_trader_coverage,
            "unique_trader_coverage_share": (
                round(unique_trader_coverage / observation_count, 4)
                if observation_count
                else None
            ),
        },
        "ingestion": {
            "last_successful_at": (
                last_success.finished_at.isoformat()
                if last_success and last_success.finished_at
                else None
            ),
            "last_successful_job_type": last_success.job_type if last_success else None,
            "failed_jobs": failed_jobs,
            "total_jobs": session.scalar(select(func.count()).select_from(IngestionJob)) or 0,
        },
        "distributions": {
            "liquidity_usd": {
                "min": _f(liquidity_stats[0]),
                "mean": _f(liquidity_stats[1]),
                "max": _f(liquidity_stats[2]),
                "non_null_observations": int(liquidity_stats[3] or 0),
                "note": (
                    "Mean is shown for completeness but liquidity is strongly "
                    "right-skewed; see the Research Lab for medians and quantiles."
                ),
            },
            "txns_h24": {
                "mean": _f(activity_stats[0]),
                "max": _f(activity_stats[1]),
                "non_null_observations": int(activity_stats[2] or 0),
            },
        },
        "dataset_versions": [
            {
                "id": v.id,
                "label": v.label,
                "pool_count": v.pool_count,
                "candle_count": v.candle_count,
                "inception_cohort_share": v.inception_cohort_share,
                "inactivity_threshold_hours": v.inactivity_threshold_hours,
                "built_at": v.built_at.isoformat() if v.built_at else None,
            }
            for v in dataset_versions
        ],
        "recent_experiments": [
            {
                "id": e.id,
                "name": e.name,
                "method": e.method,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in recent_experiments
        ],
        "data_quality_warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _data_quality_warnings(
    session: Session,
    *,
    pool_count: int,
    inception: int,
    pools_with_candles: int,
    pools_with_created_at: int,
    failed_jobs: int,
    unique_trader_coverage: int,
    observation_count: int,
) -> list[dict]:
    """Real warnings derived from real counts."""
    warnings: list[dict] = []

    if pool_count and inception / pool_count < 0.5:
        warnings.append(
            {
                "severity": "high",
                "code": "survivorship_bias",
                "message": (
                    f"{pool_count - inception} of {pool_count} pools come from ranked "
                    "listings rather than the new-pools feed. Survival statistics over "
                    "this mix are biased upward."
                ),
            }
        )
    if pool_count and pools_with_candles / pool_count < 0.5:
        warnings.append(
            {
                "severity": "medium",
                "code": "sparse_history",
                "message": (
                    f"Only {pools_with_candles} of {pool_count} pools have any OHLCV "
                    "history. Run the backfill job to extend coverage."
                ),
            }
        )
    if pool_count and pools_with_created_at < pool_count:
        warnings.append(
            {
                "severity": "medium",
                "code": "missing_launch_timestamp",
                "message": (
                    f"{pool_count - pools_with_created_at} pool(s) have no launch "
                    "timestamp and are excluded from launch-cohort analysis."
                ),
            }
        )
    if failed_jobs:
        warnings.append(
            {
                "severity": "high",
                "code": "failed_jobs",
                "message": f"{failed_jobs} ingestion job(s) failed. See /api/ingestion/jobs.",
            }
        )
    if observation_count and unique_trader_coverage / observation_count < 0.9:
        warnings.append(
            {
                "severity": "low",
                "code": "partial_unique_traders",
                "message": (
                    "Not all observations carry unique-trader counts; DexScreener does "
                    "not report them, so cross-source rows lack this field."
                ),
            }
        )
    warnings.append(
        {
            "severity": "info",
            "code": "holder_data_unavailable",
            "message": (
                "Holder-concentration metrics are not collected: the public Solana RPC "
                "rate-limits getTokenLargestAccounts. A dedicated RPC provider is "
                "required to enable them."
            ),
        }
    )
    return warnings


def _f(value) -> float | None:
    return float(value) if value is not None else None
