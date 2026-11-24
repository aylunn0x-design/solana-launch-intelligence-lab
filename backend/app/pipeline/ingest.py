"""Ingestion orchestration.

Pipeline shape:

    SOURCE -> RAW INGESTION -> RAW STORAGE -> VALIDATION -> NORMALIZATION
           -> ANALYTICAL TABLES

Every run opens an `IngestionJob`, records HTTP accounting and row counts
against it, and closes it with a real status. A failure is recorded as FAILED
with the message attached — never swallowed, never reported as success.

Idempotency: tokens key on mint, pools on pool_address, observations on
(pool, source, observed_at), candles on (pool, timeframe, ts). Re-running a job
adds new information and skips duplicates rather than creating them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import (
    DiscoveryMethod,
    IngestionJob,
    JobStatus,
    MarketObservation,
    OhlcvCandle,
    Pool,
    RawPayload,
    Timeframe,
    Token,
)
from app.pipeline.validate import validate_snapshot
from app.providers.base import ProviderError
from app.providers.geckoterminal import GeckoTerminalProvider
from app.providers.registry import get_source_id
from app.providers.types import Candle, FetchResult, PoolSnapshot

logger = logging.getLogger(__name__)


@dataclass
class IngestionOutcome:
    """What a run actually did. Returned to the API and logged."""

    job_id: int
    status: JobStatus
    pools_seen: int = 0
    pools_created: int = 0
    observations_written: int = 0
    candles_written: int = 0
    rejected: int = 0
    duplicates_skipped: int = 0
    http_requests: int = 0
    rate_limit_hits: int = 0
    errors: list[str] = field(default_factory=list)
    validation_flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "pools_seen": self.pools_seen,
            "pools_created": self.pools_created,
            "observations_written": self.observations_written,
            "candles_written": self.candles_written,
            "rejected": self.rejected,
            "duplicates_skipped": self.duplicates_skipped,
            "http_requests": self.http_requests,
            "rate_limit_hits": self.rate_limit_hits,
            "errors": self.errors[:20],
            "validation_flags": self.validation_flags[:20],
        }


class IngestionService:
    """Writes provider output into the analytical schema."""

    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    # -- job lifecycle ------------------------------------------------------

    def _open_job(self, job_type: str, source_id: int, params: dict) -> IngestionJob:
        job = IngestionJob(
            job_type=job_type,
            status=JobStatus.RUNNING,
            source_id=source_id,
            params=params,
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(job)
        self.session.commit()
        return job

    def _close_job(
        self, job: IngestionJob, outcome: IngestionOutcome, status: JobStatus,
        error: str | None = None,
    ) -> None:
        job.status = status
        job.finished_at = datetime.now(timezone.utc)
        job.records_written = outcome.observations_written + outcome.candles_written
        job.records_skipped = outcome.duplicates_skipped + outcome.rejected
        job.http_requests_made = outcome.http_requests
        job.rate_limit_hits = outcome.rate_limit_hits
        job.error_message = error
        if outcome.validation_flags:
            job.notes = f"{len(outcome.validation_flags)} validation flag(s): " + "; ".join(
                outcome.validation_flags[:10]
            )
        self.session.commit()

    # -- raw storage --------------------------------------------------------

    def _store_raw(self, job: IngestionJob, source_id: int, result: FetchResult) -> RawPayload:
        raw = RawPayload(
            job_id=job.id,
            source_id=source_id,
            endpoint=result.endpoint,
            request_params=result.params or {},
            http_status=result.http_status,
            payload=result.raw_payload,
        )
        self.session.add(raw)
        self.session.flush()
        return raw

    # -- upserts ------------------------------------------------------------

    def _get_or_create_token(self, snap: PoolSnapshot) -> Token:
        token = self.session.execute(
            select(Token).where(Token.mint == snap.base_token_mint)
        ).scalar_one_or_none()

        if token is None:
            token = Token(
                mint=snap.base_token_mint,
                symbol=snap.base_token_symbol,
                name=snap.base_token_name or snap.base_token_symbol,
                first_seen_at=datetime.now(timezone.utc),
            )
            self.session.add(token)
            self.session.flush()
            return token

        # Backfill labels if a later source knows more than the first one did.
        if not token.symbol and snap.base_token_symbol:
            token.symbol = snap.base_token_symbol
        if not token.name and (snap.base_token_name or snap.base_token_symbol):
            token.name = snap.base_token_name or snap.base_token_symbol
        return token

    def _get_or_create_pool(
        self, snap: PoolSnapshot, token: Token, source_id: int,
        discovery: DiscoveryMethod,
    ) -> tuple[Pool, bool]:
        pool = self.session.execute(
            select(Pool).where(Pool.pool_address == snap.pool_address)
        ).scalar_one_or_none()

        if pool is not None:
            # Learn pool_created_at if the first sighting lacked it.
            if pool.pool_created_at is None and snap.pool_created_at is not None:
                pool.pool_created_at = snap.pool_created_at
            if pool.dex is None and snap.dex:
                pool.dex = snap.dex
            # Discovery method is NOT overwritten: how a pool first entered the
            # dataset is a permanent property of the sample, and changing it
            # would corrupt the bias accounting.
            return pool, False

        pool = Pool(
            pool_address=snap.pool_address,
            token_id=token.id,
            network=snap.network,
            dex=snap.dex,
            pool_name=snap.pool_name,
            quote_token_mint=snap.quote_token_mint,
            pool_created_at=snap.pool_created_at,
            discovery_method=discovery,
            discovered_at=datetime.now(timezone.utc),
            source_id=source_id,
        )
        self.session.add(pool)
        self.session.flush()
        return pool, True

    def _write_observation(
        self, snap: PoolSnapshot, pool: Pool, source_id: int,
        job: IngestionJob, raw_id: int | None,
    ) -> bool:
        """Insert one observation. Returns False if it is a duplicate."""
        observed_at = snap.observed_at or datetime.now(timezone.utc)

        exists = self.session.execute(
            select(MarketObservation.id).where(
                MarketObservation.pool_id == pool.id,
                MarketObservation.source_id == source_id,
                MarketObservation.observed_at == observed_at,
            )
        ).scalar_one_or_none()
        if exists is not None:
            return False

        self.session.add(
            MarketObservation(
                pool_id=pool.id,
                source_id=source_id,
                job_id=job.id,
                raw_payload_id=raw_id,
                observed_at=observed_at,
                liquidity_usd=snap.liquidity_usd,
                fdv_usd=snap.fdv_usd,
                market_cap_usd=snap.market_cap_usd,
                price_usd=snap.price_usd,
                price_native=snap.price_native,
                volume_usd_m5=snap.volume_usd_m5,
                volume_usd_h1=snap.volume_usd_h1,
                volume_usd_h6=snap.volume_usd_h6,
                volume_usd_h24=snap.volume_usd_h24,
                txns_h1=snap.txns_h1,
                txns_h24=snap.txns_h24,
                buys_h24=snap.buys_h24,
                sells_h24=snap.sells_h24,
                unique_traders_h24=snap.unique_traders_h24,
                buyers_h24=snap.buyers_h24,
                sellers_h24=snap.sellers_h24,
                price_change_pct_h1=snap.price_change_pct_h1,
                price_change_pct_h24=snap.price_change_pct_h24,
                missing_field_count=snap.missing_field_count,
            )
        )
        return True

    def _ingest_snapshots(
        self, snapshots: list[PoolSnapshot], *, pool_source_id: int,
        discovery: DiscoveryMethod, job: IngestionJob, raw_id: int | None,
        outcome: IngestionOutcome,
    ) -> None:
        for snap in snapshots:
            outcome.pools_seen += 1
            report = validate_snapshot(snap)
            if report.rejected:
                outcome.rejected += 1
                msg = f"{snap.pool_address or '<no address>'}: {report.summary()}"
                logger.warning("Rejected snapshot — %s", msg)
                outcome.errors.append(msg)
                continue
            for flag in report.flags:
                outcome.validation_flags.append(
                    f"{snap.pool_address}: {flag.field} {flag.message}"
                )

            token = self._get_or_create_token(snap)
            pool, created = self._get_or_create_pool(snap, token, pool_source_id, discovery)
            if created:
                outcome.pools_created += 1

            if self._write_observation(snap, pool, pool_source_id, job, raw_id):
                outcome.observations_written += 1
            else:
                outcome.duplicates_skipped += 1

    # -- public operations --------------------------------------------------

    #: feed name -> (provider method name, discovery label)
    FEEDS: dict[str, tuple[str, DiscoveryMethod]] = {
        "new": ("fetch_new_pools", DiscoveryMethod.NEW_POOLS_FEED),
        "top": ("fetch_top_pools", DiscoveryMethod.TOP_POOLS),
        "trending": ("fetch_trending_pools", DiscoveryMethod.TRENDING_POOLS),
    }

    async def discover_new_pools(
        self, provider: GeckoTerminalProvider, pages: int = 1,
    ) -> IngestionOutcome:
        """Poll the new-pools feed: the unbiased inception cohort."""
        return await self.discover_pools(provider, feed="new", pages=pages)

    async def discover_pools(
        self, provider: GeckoTerminalProvider, feed: str = "new", pages: int = 1,
    ) -> IngestionOutcome:
        """Poll a discovery feed and record how each pool was found.

        `feed="new"` yields the inception cohort (no survivorship bias).
        `feed="top"` / `"trending"` yield survivorship-biased samples: a pool
        appears only because it is *currently* notable. The discovery label is
        stored per pool so analyses can separate them.
        """
        if feed not in self.FEEDS:
            raise ValueError(f"unknown feed {feed!r}; expected one of {sorted(self.FEEDS)}")
        method_name, discovery = self.FEEDS[feed]
        fetch = getattr(provider, method_name)

        source_id = get_source_id(self.session, provider.name)
        job = self._open_job(
            f"discover_pools:{feed}", source_id, {"feed": feed, "pages": pages}
        )
        outcome = IngestionOutcome(job_id=job.id, status=JobStatus.RUNNING)

        try:
            for page in range(1, pages + 1):
                try:
                    result = await fetch(page=page)
                except (ProviderError, ValueError) as exc:
                    outcome.errors.append(f"page {page}: {exc}")
                    logger.error("%s feed page %d failed: %s", feed, page, exc)
                    continue

                raw = self._store_raw(job, source_id, result)
                self._ingest_snapshots(
                    result.snapshots,
                    pool_source_id=source_id,
                    discovery=discovery,
                    job=job,
                    raw_id=raw.id,
                    outcome=outcome,
                )
                self.session.commit()

            outcome.http_requests = provider.client.stats.requests_made
            outcome.rate_limit_hits = provider.client.stats.rate_limit_hits

            if outcome.errors and outcome.observations_written == 0:
                status = JobStatus.FAILED
            elif outcome.errors:
                status = JobStatus.PARTIAL
            else:
                status = JobStatus.SUCCEEDED
            outcome.status = status
            self._close_job(job, outcome, status, "; ".join(outcome.errors[:5]) or None)
            return outcome

        except Exception as exc:  # noqa: BLE001 - recorded, re-raised
            self.session.rollback()
            outcome.status = JobStatus.FAILED
            outcome.errors.append(str(exc))
            self._close_job(job, outcome, JobStatus.FAILED, str(exc))
            logger.exception("discover_pools(%s) failed", feed)
            raise

    async def refresh_pool_snapshots(
        self, provider: GeckoTerminalProvider, pool_addresses: list[str],
    ) -> IngestionOutcome:
        """Re-poll known pools to extend the longitudinal panel."""
        source_id = get_source_id(self.session, provider.name)
        job = self._open_job(
            "refresh_pool_snapshots", source_id, {"pool_count": len(pool_addresses)}
        )
        outcome = IngestionOutcome(job_id=job.id, status=JobStatus.RUNNING)

        try:
            for address in pool_addresses:
                try:
                    result = await provider.fetch_pool(address)
                except ProviderError as exc:
                    outcome.errors.append(f"{address}: {exc}")
                    continue
                raw = self._store_raw(job, source_id, result)
                self._ingest_snapshots(
                    result.snapshots,
                    pool_source_id=source_id,
                    # Re-polling an existing pool must not relabel its origin;
                    # MANUAL applies only if this call is the first sighting.
                    discovery=DiscoveryMethod.MANUAL,
                    job=job,
                    raw_id=raw.id,
                    outcome=outcome,
                )
                self.session.commit()

            outcome.http_requests = provider.client.stats.requests_made
            outcome.rate_limit_hits = provider.client.stats.rate_limit_hits
            status = (
                JobStatus.SUCCEEDED
                if not outcome.errors
                else (JobStatus.PARTIAL if outcome.observations_written else JobStatus.FAILED)
            )
            outcome.status = status
            self._close_job(job, outcome, status, "; ".join(outcome.errors[:5]) or None)
            return outcome
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            outcome.status = JobStatus.FAILED
            self._close_job(job, outcome, JobStatus.FAILED, str(exc))
            raise

    async def backfill_ohlcv(
        self, provider: GeckoTerminalProvider, pool_addresses: list[str],
        timeframe: str = "hour", limit: int = 1000,
    ) -> IngestionOutcome:
        """Fetch historical candles for pools. This is the retrospective history."""
        source_id = get_source_id(self.session, provider.name)
        job = self._open_job(
            "backfill_ohlcv",
            source_id,
            {"pool_count": len(pool_addresses), "timeframe": timeframe, "limit": limit},
        )
        outcome = IngestionOutcome(job_id=job.id, status=JobStatus.RUNNING)
        tf_enum = Timeframe(timeframe)

        try:
            for address in pool_addresses:
                pool = self.session.execute(
                    select(Pool).where(Pool.pool_address == address)
                ).scalar_one_or_none()
                if pool is None:
                    outcome.errors.append(f"{address}: not a known pool; discover it first")
                    continue

                try:
                    result = await provider.fetch_ohlcv(
                        address, timeframe=timeframe, limit=limit
                    )
                except ProviderError as exc:
                    outcome.errors.append(f"{address}: {exc}")
                    continue

                # Raw payload is stored for provenance even though candle rows
                # link to the job rather than to an individual payload.
                self._store_raw(job, source_id, result)
                written = self._write_candles(
                    result.candles, pool, tf_enum, source_id, job, outcome
                )
                outcome.candles_written += written
                self.session.commit()
                logger.info("Backfilled %d %s candles for %s", written, timeframe, address)

            outcome.http_requests = provider.client.stats.requests_made
            outcome.rate_limit_hits = provider.client.stats.rate_limit_hits
            status = (
                JobStatus.SUCCEEDED
                if not outcome.errors
                else (JobStatus.PARTIAL if outcome.candles_written else JobStatus.FAILED)
            )
            outcome.status = status
            self._close_job(job, outcome, status, "; ".join(outcome.errors[:5]) or None)
            return outcome
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            outcome.status = JobStatus.FAILED
            self._close_job(job, outcome, JobStatus.FAILED, str(exc))
            raise

    def _write_candles(
        self, candles: list[Candle], pool: Pool, timeframe: Timeframe,
        source_id: int, job: IngestionJob, outcome: IngestionOutcome,
    ) -> int:
        if not candles:
            return 0

        existing: set[datetime] = set(
            self.session.execute(
                select(OhlcvCandle.ts).where(
                    OhlcvCandle.pool_id == pool.id, OhlcvCandle.timeframe == timeframe
                )
            ).scalars()
        )
        # SQLite returns naive datetimes; normalize before comparing.
        existing = {ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts for ts in existing}

        written = 0
        for candle in candles:
            if candle.ts in existing:
                outcome.duplicates_skipped += 1
                continue
            if candle.volume_usd is not None and candle.volume_usd < 0:
                outcome.rejected += 1
                outcome.errors.append(
                    f"{pool.pool_address}: negative volume at {candle.ts.isoformat()}"
                )
                continue
            self.session.add(
                OhlcvCandle(
                    pool_id=pool.id,
                    source_id=source_id,
                    job_id=job.id,
                    timeframe=timeframe,
                    ts=candle.ts,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume_usd=candle.volume_usd,
                )
            )
            existing.add(candle.ts)
            written += 1
        return written
