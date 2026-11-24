"""Provider tests against recorded payload shapes. No network access.

The fixtures below are trimmed copies of real responses observed from the live
APIs during development, so these tests check the parsers against the shapes
they actually have to handle.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.providers.base import ProviderError, RateLimitedClient, RateLimitExceeded, TokenBucket
from app.providers.dexscreener import BASE_URL as DEX_BASE
from app.providers.dexscreener import DexScreenerProvider
from app.providers.geckoterminal import BASE_URL as GT_BASE
from app.providers.geckoterminal import MAX_NEW_POOLS_PAGE, GeckoTerminalProvider

# --- fixtures mirroring observed live responses ----------------------------

NEW_POOLS_PAYLOAD = {
    "data": [
        {
            "id": "solana_GsVSf58XvULZYz6cWXW43qYzfrJzKNrGSwaionYibJfk",
            "type": "pool",
            "attributes": {
                "base_token_price_usd": "0.000000081888618242236932301967866371665280405",
                "base_token_price_native_currency": "0.0000000010670955016472",
                "address": "GsVSf58XvULZYz6cWXW43qYzfrJzKNrGSwaionYibJfk",
                "name": "kneegro / SOL",
                "pool_created_at": "2026-11-24T22:25:01Z",
                "fdv_usd": "163.77723648",
                "market_cap_usd": None,
                "price_change_percentage": {"m5": "0", "h1": "0", "h24": "1.5"},
                "volume_usd": {"m5": "1.0049301785", "h1": "2.5", "h6": "2.5", "h24": "2.5"},
                "reserve_in_usd": "30.8102",
                "transactions": {
                    "h1": {"buys": 7, "sells": 0, "buyers": 7, "sellers": 0, "unique_traders": 7},
                    "h24": {
                        "buys": 10,
                        "sells": 3,
                        "buyers": 8,
                        "sellers": 2,
                        "unique_traders": 9,
                    },
                },
            },
            "relationships": {
                "base_token": {
                    "data": {
                        "id": "solana_8zknH3bQTduTBKMU61ZsFMVwQLtVqPsS5pBzbzZgpump",
                        "type": "token",
                    }
                },
                "quote_token": {
                    "data": {
                        "id": "solana_So11111111111111111111111111111111111111112",
                        "type": "token",
                    }
                },
                "dex": {"data": {"id": "pumpswap", "type": "dex"}},
            },
        }
    ]
}

OHLCV_PAYLOAD = {
    "data": {
        "id": "x",
        "type": "ohlcv_request_response",
        "attributes": {
            # Provider returns newest-first.
            "ohlcv_list": [
                [1795557600, 69.35, 69.68, 68.98, 69.64, 746548.24],
                [1795554000, 68.10, 69.40, 68.00, 69.35, 500000.00],
                [1795550400, 67.50, 68.20, 67.10, 68.10, 420000.00],
            ]
        },
    }
}

DEXSCREENER_PAYLOAD = {
    "schemaVersion": "1.0.0",
    "pairs": [
        {
            "chainId": "solana",
            "dexId": "orca",
            "pairAddress": "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
            "baseToken": {
                "address": "So11111111111111111111111111111111111111112",
                "name": "Wrapped SOL",
                "symbol": "SOL",
            },
            "quoteToken": {
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "USDC",
            },
            "priceUsd": "69.59",
            "priceNative": "69.5992",
            "txns": {"h1": {"buys": 948, "sells": 1141}, "h24": {"buys": 44077, "sells": 56692}},
            "volume": {"h24": 102564324.71, "h6": 15196140.63, "h1": 1319575.47, "m5": 28250.54},
            "priceChange": {"h1": 0.22, "h24": -3.67},
            "liquidity": {"usd": 17076464.01, "base": 212061, "quote": 2317150},
            "pairCreatedAt": 1688106058000,
            "fdv": 42000000000,
            "marketCap": 38000000000,
        },
        {
            # Wrong chain: must be filtered out.
            "chainId": "ethereum",
            "dexId": "uniswap",
            "pairAddress": "0xabc",
            "baseToken": {"address": "0xdef", "symbol": "WETH"},
            "liquidity": {"usd": 1.0},
        },
    ],
}


@pytest.fixture()
def gt_provider():
    client = RateLimitedClient(GT_BASE, rate_per_min=6000, max_retries=1)
    return GeckoTerminalProvider(client)


@pytest.fixture()
def dex_provider():
    client = RateLimitedClient(DEX_BASE, rate_per_min=6000, max_retries=1)
    return DexScreenerProvider(client)


class TestGeckoTerminalParsing:
    @pytest.mark.asyncio
    @respx.mock
    async def test_parses_new_pools(self, gt_provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        result = await gt_provider.fetch_new_pools(page=1)
        assert len(result.snapshots) == 1
        snap = result.snapshots[0]

        assert snap.pool_address == "GsVSf58XvULZYz6cWXW43qYzfrJzKNrGSwaionYibJfk"
        assert snap.base_token_mint == "8zknH3bQTduTBKMU61ZsFMVwQLtVqPsS5pBzbzZgpump"
        assert snap.quote_token_mint == "So11111111111111111111111111111111111111112"
        assert snap.dex == "pumpswap"
        assert snap.base_token_symbol == "kneegro"
        assert snap.liquidity_usd == pytest.approx(30.8102)
        assert snap.fdv_usd == pytest.approx(163.77723648)
        # Observed live: market_cap_usd is often null for new tokens.
        assert snap.market_cap_usd is None
        assert snap.pool_created_at.isoformat() == "2026-11-24T22:25:01+00:00"

    @pytest.mark.asyncio
    @respx.mock
    async def test_derives_txn_totals_and_unique_traders(self, gt_provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        snap = (await gt_provider.fetch_new_pools()).snapshots[0]
        assert snap.buys_h24 == 10
        assert snap.sells_h24 == 3
        assert snap.txns_h24 == 13  # buys + sells
        assert snap.unique_traders_h24 == 9
        assert snap.txns_h1 == 7

    @pytest.mark.asyncio
    @respx.mock
    async def test_raw_payload_is_preserved(self, gt_provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=NEW_POOLS_PAYLOAD)
        )
        result = await gt_provider.fetch_new_pools()
        # Provenance: the untouched payload must come back for raw storage.
        assert result.raw_payload == NEW_POOLS_PAYLOAD

    @pytest.mark.asyncio
    @respx.mock
    async def test_ohlcv_sorted_ascending(self, gt_provider):
        url = f"{GT_BASE}/networks/solana/pools/POOL/ohlcv/hour"
        respx.get(url).mock(return_value=httpx.Response(200, json=OHLCV_PAYLOAD))
        result = await gt_provider.fetch_ohlcv("POOL", timeframe="hour")
        assert len(result.candles) == 3
        timestamps = [c.ts for c in result.candles]
        assert timestamps == sorted(timestamps), "candles must be ascending by time"
        assert result.candles[0].close == pytest.approx(68.10)
        assert result.candles[-1].volume_usd == pytest.approx(746548.24)

    @pytest.mark.asyncio
    async def test_rejects_page_beyond_provider_cap(self, gt_provider):
        # The live API answers HTTP 401 past page 10; fail with a clear reason.
        with pytest.raises(ValueError, match="pages 1"):
            await gt_provider.fetch_new_pools(page=MAX_NEW_POOLS_PAGE + 1)

    @pytest.mark.asyncio
    async def test_rejects_unknown_timeframe(self, gt_provider):
        with pytest.raises(ValueError, match="unsupported timeframe"):
            await gt_provider.fetch_ohlcv("POOL", timeframe="fortnight")

    @pytest.mark.asyncio
    @respx.mock
    async def test_pool_without_identity_is_dropped(self, gt_provider):
        broken = {"data": [{"attributes": {"name": "x / SOL"}, "relationships": {}}]}
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json=broken)
        )
        result = await gt_provider.fetch_new_pools()
        # No address and no mint: unusable, so dropped rather than half-stored.
        assert result.snapshots == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_envelope_raises(self, gt_provider):
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, json={"unexpected": True})
        )
        with pytest.raises(ProviderError, match="no 'data' key"):
            await gt_provider.fetch_new_pools()

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_ohlcv_list_raises(self, gt_provider):
        url = f"{GT_BASE}/networks/solana/pools/POOL/ohlcv/hour"
        respx.get(url).mock(return_value=httpx.Response(200, json={"data": {"attributes": {}}}))
        with pytest.raises(ProviderError, match="ohlcv_list"):
            await gt_provider.fetch_ohlcv("POOL")


class TestDexScreenerParsing:
    @pytest.mark.asyncio
    @respx.mock
    async def test_parses_pairs_and_filters_other_chains(self, dex_provider):
        mint = "So11111111111111111111111111111111111111112"
        respx.get(f"{DEX_BASE}/latest/dex/tokens/{mint}").mock(
            return_value=httpx.Response(200, json=DEXSCREENER_PAYLOAD)
        )
        result = await dex_provider.fetch_token_pairs(mint)
        # The ethereum pair must be excluded.
        assert len(result.snapshots) == 1
        snap = result.snapshots[0]
        assert snap.dex == "orca"
        assert snap.liquidity_usd == pytest.approx(17076464.01)
        assert snap.txns_h24 == 44077 + 56692

    @pytest.mark.asyncio
    @respx.mock
    async def test_epoch_millisecond_launch_time(self, dex_provider):
        mint = "So11111111111111111111111111111111111111112"
        respx.get(f"{DEX_BASE}/latest/dex/tokens/{mint}").mock(
            return_value=httpx.Response(200, json=DEXSCREENER_PAYLOAD)
        )
        snap = (await dex_provider.fetch_token_pairs(mint)).snapshots[0]
        assert snap.pool_created_at.year == 2023

    @pytest.mark.asyncio
    @respx.mock
    async def test_does_not_invent_unique_traders(self, dex_provider):
        mint = "So11111111111111111111111111111111111111112"
        respx.get(f"{DEX_BASE}/latest/dex/tokens/{mint}").mock(
            return_value=httpx.Response(200, json=DEXSCREENER_PAYLOAD)
        )
        snap = (await dex_provider.fetch_token_pairs(mint)).snapshots[0]
        # DexScreener does not report these; they must stay null, not be guessed.
        assert snap.unique_traders_h24 is None
        assert snap.buyers_h24 is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_null_pairs_is_a_valid_empty_answer(self, dex_provider):
        respx.get(f"{DEX_BASE}/latest/dex/tokens/MINT").mock(
            return_value=httpx.Response(200, json={"schemaVersion": "1.0.0", "pairs": None})
        )
        result = await dex_provider.fetch_token_pairs("MINT")
        # "no market for this mint" is information, not an error.
        assert result.snapshots == []


class TestRateLimitingAndRetries:
    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_then_succeeds_after_429(self, gt_provider):
        route = respx.get(f"{GT_BASE}/networks/solana/new_pools")
        route.side_effect = [
            httpx.Response(429, json={"status": {"error_code": 429}}),
            httpx.Response(200, json=NEW_POOLS_PAYLOAD),
        ]
        result = await gt_provider.fetch_new_pools()
        assert len(result.snapshots) == 1
        assert gt_provider.client.stats.rate_limit_hits == 1
        assert gt_provider.client.stats.retries >= 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_gives_up_and_raises_rather_than_returning_empty(self):
        client = RateLimitedClient(GT_BASE, rate_per_min=6000, max_retries=1)
        provider = GeckoTerminalProvider(client)
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(429, json={})
        )
        # Exhausted retries must raise, never return a fabricated empty result.
        with pytest.raises(RateLimitExceeded):
            await provider.fetch_new_pools()
        assert client.stats.failures == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_client_error_is_not_retried(self):
        client = RateLimitedClient(GT_BASE, rate_per_min=6000, max_retries=3)
        provider = GeckoTerminalProvider(client)
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(404, text="nope")
        )
        with pytest.raises(ProviderError) as exc:
            await provider.fetch_new_pools()
        assert exc.value.status_code == 404
        # One attempt only: 4xx is not transient.
        assert client.stats.requests_made == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_json_response_raises(self):
        client = RateLimitedClient(GT_BASE, rate_per_min=6000, max_retries=0)
        provider = GeckoTerminalProvider(client)
        respx.get(f"{GT_BASE}/networks/solana/new_pools").mock(
            return_value=httpx.Response(200, text="<html>maintenance</html>")
        )
        with pytest.raises(ProviderError, match="not valid JSON"):
            await provider.fetch_new_pools()


class TestTokenBucket:
    def test_shrinks_on_throttle_and_respects_floor(self):
        bucket = TokenBucket(100)
        bucket.shrink()
        assert bucket.rate_per_min == 50
        for _ in range(10):
            bucket.shrink()
        assert bucket.rate_per_min >= 5, "must not shrink below the floor"

    def test_recovers_after_clean_requests(self):
        """Without recovery, one early burst of 429s cripples a long backfill."""
        bucket = TokenBucket(100)
        bucket.shrink()  # -> 50
        assert bucket.rate_per_min == 50
        for _ in range(TokenBucket.RECOVERY_AFTER_CLEAN_REQUESTS):
            bucket.note_clean_request()
        assert bucket.rate_per_min > 50

    def test_never_exceeds_configured_rate(self):
        bucket = TokenBucket(10)
        for _ in range(500):
            bucket.note_clean_request()
        assert bucket.rate_per_min == 10
