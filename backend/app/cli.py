"""Command-line entry points for the data pipeline and research engine.

Every operation the platform performs is runnable from here, so a result can be
reproduced without the web UI:

    python -m app.cli init-db
    python -m app.cli ingest --feed new --pages 10
    python -m app.cli ingest --feed top --pages 5
    python -m app.cli backfill --timeframe hour --limit-pools 50
    python -m app.cli build-dataset --label v1 --cohort all
    python -m app.cli status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.models import (
    Base,
    DatasetVersion,
    DiscoveryMethod,
    IngestionJob,
    MarketObservation,
    OhlcvCandle,
    Pool,
    Timeframe,
    Token,
)
from app.pipeline.ingest import IngestionService
from app.providers.registry import build_primary_provider, sync_sources

logger = logging.getLogger("app.cli")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every request at INFO; too noisy for bulk ingestion.
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_init_db(_args) -> int:
    """Create tables directly from the models (development convenience).

    For managed schema changes use Alembic: `alembic upgrade head`.
    """
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        ids = sync_sources(session)
    print(f"Created {len(Base.metadata.tables)} tables.")
    print(f"Registered data sources: {ids}")
    return 0


async def _ingest(feed: str, pages: int) -> int:
    settings = get_settings()
    provider = build_primary_provider(settings)
    try:
        with SessionLocal() as session:
            service = IngestionService(session, settings)
            outcome = await service.discover_pools(provider, feed=feed, pages=pages)
            print(json.dumps(outcome.as_dict(), indent=2))
            return 0 if outcome.status.value in {"succeeded", "partial"} else 1
    finally:
        await provider.aclose()


def cmd_ingest(args) -> int:
    return asyncio.run(_ingest(args.feed, args.pages))


async def _backfill(
    timeframe: str,
    limit_pools: int,
    cohort: str,
    min_age_minutes: int,
    max_age_days: float | None = None,
    order: str = "oldest",
    only_missing: bool = False,
) -> int:
    settings = get_settings()
    provider = build_primary_provider(settings)
    try:
        with SessionLocal() as session:
            stmt = select(Pool.pool_address).where(Pool.pool_created_at.is_not(None))
            if cohort == "inception":
                stmt = stmt.where(Pool.discovery_method == DiscoveryMethod.NEW_POOLS_FEED)
            elif cohort == "retrospective":
                stmt = stmt.where(Pool.discovery_method != DiscoveryMethod.NEW_POOLS_FEED)

            now = datetime.now(timezone.utc)

            # Pools only minutes old have no candles worth fetching; skip them
            # so the rate-limit budget goes to pools that carry history.
            if min_age_minutes > 0:
                stmt = stmt.where(
                    Pool.pool_created_at
                    <= datetime.fromtimestamp(
                        now.timestamp() - min_age_minutes * 60, tz=timezone.utc
                    )
                )
            # Upper bound keeps the sample about *launches* rather than mixing in
            # pools that have existed for years.
            if max_age_days is not None:
                stmt = stmt.where(
                    Pool.pool_created_at
                    >= datetime.fromtimestamp(
                        now.timestamp() - max_age_days * 86400, tz=timezone.utc
                    )
                )
            if only_missing:
                # Skip pools that already have hourly candles, so repeated runs
                # extend coverage instead of re-fetching the same pools.
                have_candles = select(OhlcvCandle.pool_id).where(
                    OhlcvCandle.timeframe == Timeframe.HOUR
                )
                stmt = stmt.where(Pool.id.not_in(have_candles))

            stmt = stmt.order_by(
                Pool.pool_created_at.asc() if order == "oldest" else Pool.pool_created_at.desc()
            ).limit(limit_pools)
            addresses = list(session.execute(stmt).scalars())

            if not addresses:
                print("No pools matched the selection; nothing to backfill.")
                return 0

            print(f"Backfilling {timeframe} candles for {len(addresses)} pool(s)...")
            service = IngestionService(session, settings)
            outcome = await service.backfill_ohlcv(
                provider, addresses, timeframe=timeframe, limit=1000
            )
            print(json.dumps(outcome.as_dict(), indent=2))
            return 0 if outcome.status.value in {"succeeded", "partial"} else 1
    finally:
        await provider.aclose()


def cmd_backfill(args) -> int:
    return asyncio.run(
        _backfill(
            args.timeframe,
            args.limit_pools,
            args.cohort,
            args.min_age_minutes,
            max_age_days=args.max_age_days,
            order=args.order,
            only_missing=args.only_missing,
        )
    )


def cmd_build_dataset(args) -> int:
    from app.pipeline.features import build_dataset_version

    with SessionLocal() as session:
        version = build_dataset_version(
            session,
            label=args.label,
            cohort=args.cohort,
            inactivity_threshold_hours=args.inactivity_hours,
            min_candles=args.min_candles,
            description=args.description,
        )
        print(
            json.dumps(
                {
                    "dataset_version_id": version.id,
                    "label": version.label,
                    "pool_count": version.pool_count,
                    "observation_count": version.observation_count,
                    "candle_count": version.candle_count,
                    "inception_cohort_share": version.inception_cohort_share,
                    "inactivity_threshold_hours": version.inactivity_threshold_hours,
                    "criteria": version.criteria,
                },
                indent=2,
            )
        )
    return 0


def cmd_status(_args) -> int:
    with SessionLocal() as session:
        counts = {
            "tokens": session.scalar(select(func.count()).select_from(Token)),
            "pools": session.scalar(select(func.count()).select_from(Pool)),
            "market_observations": session.scalar(
                select(func.count()).select_from(MarketObservation)
            ),
            "ohlcv_candles": session.scalar(select(func.count()).select_from(OhlcvCandle)),
            "ingestion_jobs": session.scalar(select(func.count()).select_from(IngestionJob)),
            "dataset_versions": session.scalar(select(func.count()).select_from(DatasetVersion)),
        }
        by_discovery = dict(
            session.execute(
                select(Pool.discovery_method, func.count()).group_by(Pool.discovery_method)
            ).all()
        )
        print(json.dumps(counts, indent=2))
        print("pools by discovery method:")
        for method, n in by_discovery.items():
            label = method.value if hasattr(method, "value") else str(method)
            print(f"  {label:18s} {n}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create tables and register data sources").set_defaults(
        func=cmd_init_db
    )

    p_ing = sub.add_parser("ingest", help="poll a discovery feed")
    p_ing.add_argument(
        "--feed",
        choices=["new", "top", "trending"],
        default="new",
        help="'new' = unbiased inception cohort; 'top'/'trending' = survivorship-biased",
    )
    p_ing.add_argument(
        "--pages", type=int, default=1, help="pages to fetch (new_pools caps at 10)"
    )
    p_ing.set_defaults(func=cmd_ingest)

    p_bf = sub.add_parser("backfill", help="fetch historical OHLCV candles")
    p_bf.add_argument("--timeframe", choices=["minute", "hour", "day"], default="hour")
    p_bf.add_argument("--limit-pools", type=int, default=25)
    p_bf.add_argument("--cohort", choices=["all", "inception", "retrospective"], default="all")
    p_bf.add_argument(
        "--min-age-minutes",
        type=int,
        default=60,
        help="skip pools younger than this (they have no history yet)",
    )
    p_bf.add_argument(
        "--max-age-days",
        type=float,
        default=None,
        help="skip pools older than this, to keep the sample about launches",
    )
    p_bf.add_argument(
        "--order",
        choices=["oldest", "newest"],
        default="oldest",
        help="'oldest' favours pools with the most history",
    )
    p_bf.add_argument(
        "--only-missing",
        action="store_true",
        help="skip pools that already have hourly candles",
    )
    p_bf.set_defaults(func=cmd_backfill)

    p_ds = sub.add_parser("build-dataset", help="compute features into a dataset version")
    p_ds.add_argument("--label", required=True)
    p_ds.add_argument("--cohort", choices=["all", "inception", "retrospective"], default="all")
    p_ds.add_argument("--inactivity-hours", type=int, default=24)
    p_ds.add_argument("--min-candles", type=int, default=6)
    p_ds.add_argument("--description", default=None)
    p_ds.set_defaults(func=cmd_build_dataset)

    sub.add_parser("status", help="row counts and cohort composition").set_defaults(
        func=cmd_status
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
