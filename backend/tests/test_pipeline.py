"""Ingestion pipeline and validation tests. No network access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.models import (
    DiscoveryMethod,
    IngestionJob,
    JobStatus,
    MarketObservation,
    OhlcvCandle,
    Pool,
    RawPayload,
    Token,
)
from app.pipeline.ingest import IngestionService
from app.pipeline.validate import (
    Severity,
    compare_sources,
    looks_like_solana_address,
    validate_snapshot,
)
from app.providers.base import RateLimitedClient
from app.providers.geckoterminal import BASE_URL as GT_BASE
from app.providers.geckoterminal import GeckoTerminalProvider
from app.providers.types import PoolSnapshot
from tests.test_providers import NEW_POOLS_PAYLOAD, OHLCV_PAYLOAD

VALID_POOL = "GsVSf58XvULZYz6cWXW43qYzfrJzKNrGSwaionYibJfk"
VALID_MINT = "8zknH3bQTduTBKMU61ZsFMVwQLtVqPsS5pBzbzZgpump"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def snapshot(**overrides) -> PoolSnapshot:
    defaults = dict(
        pool_address=VALID_POOL,
        base_token_mint=VALID_MINT,
        observed_at=NOW,
        pool_created_at=NOW - timedelta(hours=1),
        liquidity_usd=1000.0,
        price_usd=0.5,
        volume_usd_h24=500.0,
        txns_h24=20,
        unique_traders_h24=10,
    )
    defaults.update(overrides)
    return PoolSnapshot(**defaults)


class TestAddressValidation:
    def test_accepts_real_solana_addresses(self):
        assert looks_like_solana_address(VALID_POOL)
        assert looks_like_solana_address("So11111111111111111111111111111111111111112")

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "",
            "tooshort",
            "0x1234567890abcdef1234567890abcdef12345678",  # ethereum hex
            "OOOO" * 10,  # contains characters absent from base58
            "l" + "1" * 40,  # lowercase L is not in base58
        ],
    )
    def test_rejects_non_solana_addresses(self, bad):
        assert not looks_like_solana_address(bad)


class TestValidation:
    def test_valid_snapshot_passes(self):
        assert validate_snapshot(snapshot(), now=NOW).rejected is False

    def test_rejects_missing_identity(self):
        report = validate_snapshot(snapshot(pool_address="nope"), now=NOW)
        assert report.rejected
        assert any(i.field == "pool_address" for i in report.issues)

    def test_rejects_future_launch_timestamp(self):
        report = validate_snapshot(
            snapshot(pool_created_at=NOW + timedelta(days=1)), now=NOW
        )
        assert report.rejected

    def test_allows_small_clock_skew(self):
        report = validate_snapshot(
            snapshot(pool_created_at=NOW + timedelta(minutes=2)), now=NOW
        )
        assert report.rejected is False, "small skew between clocks is normal"

    def test_rejects_prehistoric_launch(self):
        report = validate_snapshot(
            snapshot(pool_created_at=datetime(2015, 1, 1, tzinfo=timezone.utc)), now=NOW
        )
        assert report.rejected

    def test_rejects_negative_values(self):
        assert validate_snapshot(snapshot(liquidity_usd=-5.0), now=NOW).rejected
        assert validate_snapshot(snapshot(volume_usd_h24=-1.0), now=NOW).rejected
        assert validate_snapshot(snapshot(txns_h24=-3), now=NOW).rejected

    def test_flags_but_keeps_implausible_liquidity(self):
        report = validate_snapshot(snapshot(liquidity_usd=1e15), now=NOW)
        assert report.rejected is False, "flag for review, do not silently discard"
        assert any(i.severity is Severity.FLAG for i in report.issues)

    def test_flags_more_traders_than_transactions(self):
        report = validate_snapshot(snapshot(txns_h24=5, unique_traders_h24=50), now=NOW)
        assert any(i.field == "unique_traders_h24" for i in report.flags)

    def test_flags_nested_window_inconsistency(self):
        # 24h volume cannot be below 1h volume
        report = validate_snapshot(snapshot(volume_usd_h1=100.0, volume_usd_h24=10.0), now=NOW)
        assert any("volume_usd_h24" == i.field for i in report.flags)

    def test_zero_is_valid_and_distinct_from_missing(self):
        report = validate_snapshot(snapshot(liquidity_usd=0.0, volume_usd_h24=0.0), now=NOW)
        assert report.rejected is False


class TestCrossSourceAgreement:
    def test_agreeing_sources(self):
        a = snapshot(liquidity_usd=1000.0, price_usd=1.0, volume_usd_h24=500.0)
        b = snapshot(liquidity_usd=1050.0, price_usd=1.02, volume_usd_h24=520.0)
        assert all(r.agrees for r in compare_sources(a, b))

    def test_detects_disagreement(self):
        a = snapshot(liquidity_usd=1000.0)
        b = snapshot(liquidity_usd=100000.0)  # off by 100x: unit change?
        results = {r.field: r for r in compare_sources(a, b)}
        assert results["liquidity_usd"].agrees is False
        assert "differ" in results["liquidity_usd"].note

    def test_missing_values_are_not_disagreement(self):
        a = snapshot(liquidity_usd=1000.0)
        b = snapshot(liquidity_usd=None)
        result = {r.field: r for r in compare_sources(a, b)}["liquidity_usd"]
        assert result.agrees is True
        assert "missing" in result.note


@pytest.fixture()
def provider():
    client = RateLimitedClient(GT_BASE, rate_per_min=6000, max_retries=1)
    return GeckoTerminalProvider(client)


class TestIngestionService:
    @pytest.mark.asyncio
    @respx.mock
    async def test_discovery_writes_token_pool_observation_and_raw(
        self, seeded_session, provider
    ):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        outcome = await service.discover_pools(provider, feed="new", pages=1)

        assert outcome.status is JobStatus.SUCCEEDED
        assert outcome.pools_created == 1
        assert outcome.observations_written == 1
        assert seeded_session.scalar(select(func.count()).select_from(Token)) == 1
        assert seeded_session.scalar(select(func.count()).select_from(Pool)) == 1
        # Raw payload preserved for reproducibility.
        assert seeded_session.scalar(select(func.count()).select_from(RawPayload)) == 1

        pool = seeded_session.execute(select(Pool)).scalar_one()
        assert pool.discovery_method is DiscoveryMethod.NEW_POOLS_FEED
        assert pool.dex == "pumpswap"

    @pytest.mark.asyncio
    @respx.mock
    async def test_job_is_recorded_with_accounting(self, seeded_session, provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        outcome = await service.discover_pools(provider, feed="new", pages=1)

        job = seeded_session.get(IngestionJob, outcome.job_id)
        assert job.status is JobStatus.SUCCEEDED
        assert job.finished_at is not None
        assert job.http_requests_made >= 1
        assert job.records_written == 1
        assert job.error_message is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_top_feed_is_labelled_survivorship_biased(self, seeded_session, provider):
        respx.get(f"{GT_BASE}/networks/solana/pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        await service.discover_pools(provider, feed="top", pages=1)

        pool = seeded_session.execute(select(Pool)).scalar_one()
        assert pool.discovery_method is DiscoveryMethod.TOP_POOLS
        assert pool.discovery_method.is_survivorship_biased is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_rerun_does_not_duplicate_pools(self, seeded_session, provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        await service.discover_pools(provider, feed="new", pages=1)
        second = await service.discover_pools(provider, feed="new", pages=1)
        assert second.pools_created == 0, "pool identity is idempotent"
        assert seeded_session.scalar(select(func.count()).select_from(Pool)) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_discovery_method_is_never_overwritten(self, seeded_session, provider):
        """How a pool entered the sample is a permanent property of the dataset."""
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        respx.get(f"{GT_BASE}/networks/solana/pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        await service.discover_pools(provider, feed="new", pages=1)
        await service.discover_pools(provider, feed="top", pages=1)

        pool = seeded_session.execute(select(Pool)).scalar_one()
        assert pool.discovery_method is DiscoveryMethod.NEW_POOLS_FEED

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_rows_are_rejected_and_counted(self, seeded_session, provider):
        bad = {
            "data": [
                {
                    "id": "solana_bad",
                    "attributes": {
                        "address": "not-a-valid-address",
                        "name": "bad / SOL",
                        "reserve_in_usd": "100",
                    },
                    "relationships": {
                        "base_token": {"data": {"id": "solana_also-bad"}},
                    },
                }
            ]
        }
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=bad)
        )
        service = IngestionService(seeded_session)
        outcome = await service.discover_pools(provider, feed="new", pages=1)
        assert outcome.rejected == 1
        assert outcome.observations_written == 0
        assert seeded_session.scalar(select(func.count()).select_from(Pool)) == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_provider_failure_marks_job_failed_not_silent(
        self, seeded_session, provider
    ):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(500, text="upstream exploded")
        )
        service = IngestionService(seeded_session)
        outcome = await service.discover_pools(provider, feed="new", pages=1)

        assert outcome.status is JobStatus.FAILED
        assert outcome.errors, "the error must be recorded, not swallowed"
        job = seeded_session.get(IngestionJob, outcome.job_id)
        assert job.status is JobStatus.FAILED
        assert job.error_message

    @pytest.mark.asyncio
    @respx.mock
    async def test_backfill_writes_candles_and_dedupes(self, seeded_session, provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        respx.get(f"{GT_BASE}/networks/solana/pools/{VALID_POOL}/ohlcv/hour").mock(
            return_value=httpx.Response(200, json=OHLCV_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        await service.discover_pools(provider, feed="new", pages=1)

        first = await service.backfill_ohlcv(provider, [VALID_POOL], timeframe="hour")
        assert first.candles_written == 3
        assert seeded_session.scalar(select(func.count()).select_from(OhlcvCandle)) == 3

        second = await service.backfill_ohlcv(provider, [VALID_POOL], timeframe="hour")
        assert second.candles_written == 0, "same candles must not be stored twice"
        assert second.duplicates_skipped == 3
        assert seeded_session.scalar(select(func.count()).select_from(OhlcvCandle)) == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_backfill_for_unknown_pool_reports_error(self, seeded_session, provider):
        service = IngestionService(seeded_session)
        outcome = await service.backfill_ohlcv(provider, ["UnknownPoolAddress"], "hour")
        assert outcome.candles_written == 0
        assert any("not a known pool" in e for e in outcome.errors)

    @pytest.mark.asyncio
    @respx.mock
    async def test_repeated_polling_builds_a_panel(self, seeded_session, provider):
        """Two polls at different instants are two observations, not duplicates."""
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        service = IngestionService(seeded_session)
        await service.discover_pools(provider, feed="new", pages=1)
        await service.discover_pools(provider, feed="new", pages=1)
        count = seeded_session.scalar(select(func.count()).select_from(MarketObservation))
        assert count == 2
