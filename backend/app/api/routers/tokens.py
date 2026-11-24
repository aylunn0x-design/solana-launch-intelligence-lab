"""Token explorer: search, filter, sort, paginate, and per-token detail."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import (
    DiscoveryMethod,
    HolderSnapshot,
    MarketObservation,
    OhlcvCandle,
    Pool,
    Timeframe,
    Token,
)

router = APIRouter(tags=["tokens"])

SORTABLE = {
    "launch": Pool.pool_created_at,
    "discovered": Pool.discovered_at,
    "liquidity": None,  # handled via the latest-observation join
    "symbol": Token.symbol,
}


@router.get("/tokens")
def list_tokens(
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="Search by token name, symbol or mint address"),
    dex: str | None = Query(None, description="Filter by DEX/venue"),
    cohort: Literal["all", "inception", "retrospective"] = "all",
    launched_after: datetime | None = Query(None),
    launched_before: datetime | None = Query(None),
    max_age_hours: float | None = Query(None, gt=0, description="Only pools younger than this"),
    min_liquidity_usd: float | None = Query(None, ge=0),
    has_history: bool | None = Query(None, description="Only pools with OHLCV candles"),
    sort: Literal["launch", "discovered", "liquidity", "symbol"] = "launch",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    """Paginated pool/token list.

    One row per pool (the unit of a launch), joined to its token and its most
    recent observation. Pools, not tokens, because a token can launch several
    pools and each has its own lifecycle.
    """
    stmt = select(Pool, Token).join(Token, Pool.token_id == Token.id)

    if q:
        term = f"%{q.strip()}%"
        # Parameterised LIKE: no string interpolation into SQL anywhere.
        stmt = stmt.where(
            or_(
                Token.symbol.ilike(term),
                Token.name.ilike(term),
                Token.mint.ilike(term),
                Pool.pool_address.ilike(term),
            )
        )
    if dex:
        stmt = stmt.where(Pool.dex == dex)
    if cohort == "inception":
        stmt = stmt.where(Pool.discovery_method == DiscoveryMethod.NEW_POOLS_FEED)
    elif cohort == "retrospective":
        stmt = stmt.where(Pool.discovery_method != DiscoveryMethod.NEW_POOLS_FEED)
    if launched_after:
        stmt = stmt.where(Pool.pool_created_at >= launched_after)
    if launched_before:
        stmt = stmt.where(Pool.pool_created_at <= launched_before)
    if max_age_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        stmt = stmt.where(Pool.pool_created_at >= cutoff)
    if has_history is True:
        stmt = stmt.where(Pool.id.in_(select(OhlcvCandle.pool_id).distinct()))
    elif has_history is False:
        stmt = stmt.where(Pool.id.not_in(select(OhlcvCandle.pool_id).distinct()))

    if min_liquidity_usd is not None:
        stmt = stmt.where(
            Pool.id.in_(
                select(MarketObservation.pool_id).where(
                    MarketObservation.liquidity_usd >= min_liquidity_usd
                )
            )
        )

    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    if sort == "liquidity":
        # Order by each pool's most recent liquidity reading, via a correlated
        # scalar subquery that takes the newest row directly. (Matching on
        # observed_at == MAX(observed_at) inside the same subquery is invalid
        # aggregate use in SQLite.)
        sort_expr = (
            select(MarketObservation.liquidity_usd)
            .where(MarketObservation.pool_id == Pool.id)
            .order_by(MarketObservation.observed_at.desc())
            .limit(1)
            .scalar_subquery()
        )
    else:
        sort_expr = SORTABLE[sort]

    stmt = stmt.order_by(sort_expr.desc() if order == "desc" else sort_expr.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = session.execute(stmt).all()

    items = []
    for pool, token in rows:
        obs = session.execute(
            select(MarketObservation)
            .where(MarketObservation.pool_id == pool.id)
            .order_by(MarketObservation.observed_at.desc())
            .limit(1)
        ).scalars().first()
        candle_count = (
            session.scalar(
                select(func.count())
                .select_from(OhlcvCandle)
                .where(OhlcvCandle.pool_id == pool.id)
            )
            or 0
        )
        obs_count = (
            session.scalar(
                select(func.count())
                .select_from(MarketObservation)
                .where(MarketObservation.pool_id == pool.id)
            )
            or 0
        )
        items.append(
            {
                "pool_address": pool.pool_address,
                "mint": token.mint,
                "symbol": token.symbol,
                "name": token.name,
                "dex": pool.dex,
                "pool_name": pool.pool_name,
                "pool_created_at": (
                    pool.pool_created_at.isoformat() if pool.pool_created_at else None
                ),
                "age_hours": _age_hours(pool.pool_created_at),
                "discovery_method": pool.discovery_method.value
                if hasattr(pool.discovery_method, "value")
                else str(pool.discovery_method),
                "survivorship_biased": _is_biased(pool.discovery_method),
                "latest": _observation_summary(obs),
                "data_completeness": {
                    "observations": obs_count,
                    "candles": candle_count,
                    "has_launch_timestamp": pool.pool_created_at is not None,
                    "has_history": candle_count > 0,
                },
            }
        )

    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if page_size else 0,
            "has_next": page * page_size < total,
        },
        "filters_applied": {
            "q": q,
            "dex": dex,
            "cohort": cohort,
            "launched_after": launched_after.isoformat() if launched_after else None,
            "launched_before": launched_before.isoformat() if launched_before else None,
            "max_age_hours": max_age_hours,
            "min_liquidity_usd": min_liquidity_usd,
            "has_history": has_history,
            "sort": sort,
            "order": order,
        },
    }


@router.get("/tokens/dexes")
def list_dexes(session: Session = Depends(get_session)) -> dict:
    """Distinct venues present in the data, for building filter controls."""
    rows = session.execute(
        select(Pool.dex, func.count())
        .where(Pool.dex.is_not(None))
        .group_by(Pool.dex)
        .order_by(func.count().desc())
    ).all()
    return {"dexes": [{"dex": d, "pool_count": int(c)} for d, c in rows]}


@router.get("/tokens/{identifier}")
def token_detail(identifier: str, session: Session = Depends(get_session)) -> dict:
    """Detail for a pool address or a mint address.

    Accepts either so links from charts (which key on pools) and from search
    (which may key on mints) both work.
    """
    pool = session.execute(
        select(Pool).where(Pool.pool_address == identifier)
    ).scalars().first()

    token: Token | None
    if pool is not None:
        token = session.get(Token, pool.token_id)
    else:
        token = session.execute(
            select(Token).where(Token.mint == identifier)
        ).scalars().first()
        if token is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No pool or token found for {identifier!r}. It may not have been "
                    "ingested yet."
                ),
            )
        # A token may have several pools; present the earliest-launched one.
        pool = session.execute(
            select(Pool)
            .where(Pool.token_id == token.id)
            .order_by(Pool.pool_created_at.asc().nulls_last())
            .limit(1)
        ).scalars().first()

    if pool is None or token is None:
        raise HTTPException(status_code=404, detail=f"No pool data for {identifier!r}.")

    sibling_pools = session.execute(
        select(Pool).where(Pool.token_id == token.id)
    ).scalars().all()

    observations = session.execute(
        select(MarketObservation)
        .where(MarketObservation.pool_id == pool.id)
        .order_by(MarketObservation.observed_at.asc())
    ).scalars().all()

    candle_counts = dict(
        session.execute(
            select(OhlcvCandle.timeframe, func.count())
            .where(OhlcvCandle.pool_id == pool.id)
            .group_by(OhlcvCandle.timeframe)
        ).all()
    )

    holder_rows = session.execute(
        select(HolderSnapshot)
        .where(HolderSnapshot.token_id == token.id)
        .order_by(HolderSnapshot.observed_at.desc())
        .limit(10)
    ).scalars().all()

    missing: list[str] = []
    if pool.pool_created_at is None:
        missing.append(
            "No launch timestamp: this pool cannot be placed on the launch clock, so it "
            "is excluded from launch-cohort and survival analysis."
        )
    if not candle_counts:
        missing.append(
            "No OHLCV history stored. Run the backfill job for this pool to populate it."
        )
    if not holder_rows:
        missing.append(
            "No holder-concentration snapshots. Holder collection is disabled because "
            "the public Solana RPC rate-limits getTokenLargestAccounts."
        )
    if len(observations) < 2:
        missing.append(
            f"Only {len(observations)} market observation(s): liquidity variability "
            "cannot be computed from a single point."
        )

    return {
        "token": {
            "mint": token.mint,
            "symbol": token.symbol,
            "name": token.name,
            "first_seen_at": token.first_seen_at.isoformat() if token.first_seen_at else None,
        },
        "pool": {
            "pool_address": pool.pool_address,
            "pool_name": pool.pool_name,
            "network": pool.network,
            "dex": pool.dex,
            "quote_token_mint": pool.quote_token_mint,
            "pool_created_at": (
                pool.pool_created_at.isoformat() if pool.pool_created_at else None
            ),
            "age_hours": _age_hours(pool.pool_created_at),
            "discovery_method": pool.discovery_method.value
            if hasattr(pool.discovery_method, "value")
            else str(pool.discovery_method),
            "survivorship_biased": _is_biased(pool.discovery_method),
            "discovered_at": pool.discovered_at.isoformat() if pool.discovered_at else None,
        },
        "other_pools_for_token": [
            {
                "pool_address": p.pool_address,
                "dex": p.dex,
                "pool_created_at": p.pool_created_at.isoformat() if p.pool_created_at else None,
            }
            for p in sibling_pools
            if p.id != pool.id
        ],
        "observation_summary": {
            "count": len(observations),
            "first_observed_at": (
                observations[0].observed_at.isoformat() if observations else None
            ),
            "last_observed_at": (
                observations[-1].observed_at.isoformat() if observations else None
            ),
        },
        "latest": _observation_summary(observations[-1] if observations else None),
        "candle_counts": {
            (tf.value if hasattr(tf, "value") else str(tf)): int(c)
            for tf, c in candle_counts.items()
        },
        "holder_snapshots": [
            {
                "observed_at": h.observed_at.isoformat(),
                "top1_share": h.top1_share,
                "top10_share": h.top10_share,
                "herfindahl_index": h.herfindahl_index,
                "holder_count": h.holder_count,
            }
            for h in holder_rows
        ],
        "holder_data_available": bool(holder_rows),
        "missing_data_warnings": missing,
        "provenance": _provenance(session, pool),
    }


@router.get("/tokens/{identifier}/history")
def token_history(
    identifier: str,
    session: Session = Depends(get_session),
    timeframe: Literal["minute", "hour", "day"] = "hour",
    limit: int = Query(1000, ge=1, le=5000),
) -> dict:
    """Liquidity/activity panel plus OHLCV price history for a pool."""
    pool = session.execute(
        select(Pool).where(Pool.pool_address == identifier)
    ).scalars().first()
    if pool is None:
        token = session.execute(
            select(Token).where(Token.mint == identifier)
        ).scalars().first()
        if token is None:
            raise HTTPException(status_code=404, detail=f"Unknown token or pool {identifier!r}.")
        pool = session.execute(
            select(Pool)
            .where(Pool.token_id == token.id)
            .order_by(Pool.pool_created_at.asc().nulls_last())
            .limit(1)
        ).scalars().first()
        if pool is None:
            raise HTTPException(status_code=404, detail=f"No pool for token {identifier!r}.")

    observations = session.execute(
        select(MarketObservation)
        .where(MarketObservation.pool_id == pool.id)
        .order_by(MarketObservation.observed_at.asc())
        .limit(limit)
    ).scalars().all()

    candles = session.execute(
        select(OhlcvCandle)
        .where(OhlcvCandle.pool_id == pool.id, OhlcvCandle.timeframe == Timeframe(timeframe))
        .order_by(OhlcvCandle.ts.asc())
        .limit(limit)
    ).scalars().all()

    notes: list[str] = []
    if not candles:
        notes.append(
            f"No {timeframe} candles stored for this pool. Price history is unavailable, "
            "not zero."
        )
    if len(observations) < 2:
        notes.append(
            "Fewer than two market observations: the liquidity series is a single point, "
            "so no trend can be read from it."
        )

    return {
        "pool_address": pool.pool_address,
        "pool_created_at": pool.pool_created_at.isoformat() if pool.pool_created_at else None,
        "timeframe": timeframe,
        "liquidity_activity_series": [
            {
                "observed_at": o.observed_at.isoformat(),
                "liquidity_usd": o.liquidity_usd,
                "price_usd": o.price_usd,
                "fdv_usd": o.fdv_usd,
                "volume_usd_h24": o.volume_usd_h24,
                "txns_h24": o.txns_h24,
                "unique_traders_h24": o.unique_traders_h24,
                "buys_h24": o.buys_h24,
                "sells_h24": o.sells_h24,
                "source_id": o.source_id,
            }
            for o in observations
        ],
        "ohlcv": [
            {
                "ts": c.ts.isoformat(),
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume_usd": c.volume_usd,
            }
            for c in candles
        ],
        "counts": {"observations": len(observations), "candles": len(candles)},
        "notes": notes,
    }


# --- helpers ---------------------------------------------------------------


def _age_hours(created: datetime | None) -> float | None:
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - created).total_seconds() / 3600.0, 3)


def _is_biased(method) -> bool:
    try:
        return DiscoveryMethod(method).is_survivorship_biased
    except (ValueError, TypeError):
        return True


def _observation_summary(obs: MarketObservation | None) -> dict | None:
    if obs is None:
        return None
    return {
        "observed_at": obs.observed_at.isoformat(),
        "liquidity_usd": obs.liquidity_usd,
        "price_usd": obs.price_usd,
        "fdv_usd": obs.fdv_usd,
        "volume_usd_h24": obs.volume_usd_h24,
        "txns_h24": obs.txns_h24,
        "unique_traders_h24": obs.unique_traders_h24,
        "price_change_pct_h24": obs.price_change_pct_h24,
        "missing_field_count": obs.missing_field_count,
    }


def _provenance(session: Session, pool: Pool) -> dict:
    """Which sources contributed to this pool's rows."""
    from app.models import DataSource

    source_rows = session.execute(
        select(DataSource.name, func.count(MarketObservation.id))
        .join(MarketObservation, MarketObservation.source_id == DataSource.id)
        .where(MarketObservation.pool_id == pool.id)
        .group_by(DataSource.name)
    ).all()
    discovery_source = session.get(DataSource, pool.source_id)
    return {
        "discovered_via_source": discovery_source.name if discovery_source else None,
        "observation_sources": [
            {"source": name, "observations": int(count)} for name, count in source_rows
        ],
        "note": (
            "Every stored observation records the source it came from and the raw "
            "payload it was parsed from, so any value here can be traced back to the "
            "original API response."
        ),
    }
