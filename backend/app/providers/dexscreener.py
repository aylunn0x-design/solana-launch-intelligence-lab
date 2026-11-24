"""DexScreener provider — secondary source, used for cross-source validation.

Verified live against https://api.dexscreener.com (no API key):
  * GET /latest/dex/tokens/{mint}  -> up to 30 pairs for the mint

Why a second source: it measures the same quantities (liquidity, volume,
transaction counts) independently. Disagreement between the two is a signal that
one provider is stale or has changed units, which `pipeline/validate.py` checks.
DexScreener does not report unique trader counts, so it cannot replace
GeckoTerminal as primary.

Unit note: `pairCreatedAt` is epoch **milliseconds** here, whereas GeckoTerminal
sends an ISO-8601 string. `parsing.to_utc_datetime` handles both.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.providers.base import ProviderError, RateLimitedClient
from app.providers.parsing import dig, to_float, to_int, to_utc_datetime
from app.providers.types import FetchResult, PoolSnapshot

logger = logging.getLogger(__name__)

SOURCE_NAME = "dexscreener"
BASE_URL = "https://api.dexscreener.com"

_EXPECTED_FIELDS = ("liquidity_usd", "price_usd", "volume_usd_h24", "txns_h24")


class DexScreenerProvider:
    name = SOURCE_NAME
    base_url = BASE_URL
    requires_api_key = False

    def __init__(self, client: RateLimitedClient, network: str = "solana"):
        self.client = client
        self.network = network

    @classmethod
    def build(cls, *, rate_per_min: int, timeout: float = 25.0, max_retries: int = 4,
              network: str = "solana") -> "DexScreenerProvider":
        client = RateLimitedClient(
            BASE_URL,
            rate_per_min=rate_per_min,
            timeout=timeout,
            max_retries=max_retries,
            headers={"Accept": "application/json"},
        )
        return cls(client, network=network)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def fetch_token_pairs(self, mint: str) -> FetchResult:
        """All pairs DexScreener knows for a mint, filtered to our network."""
        path = f"/latest/dex/tokens/{mint}"
        resp = await self.client.get_json(path)
        payload = resp.payload
        if not isinstance(payload, dict):
            raise ProviderError("expected a JSON object", endpoint=path)

        pairs = payload.get("pairs")
        # A mint with no pairs returns pairs: null. That is a valid, meaningful
        # answer ("no market"), not an error.
        snapshots: list[PoolSnapshot] = []
        if isinstance(pairs, list):
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                if pair.get("chainId") and pair.get("chainId") != self.network:
                    continue
                parsed = self._parse_pair(pair, subject_mint=mint)
                if parsed is not None:
                    snapshots.append(parsed)

        return FetchResult(
            endpoint=path,
            params={},
            http_status=resp.status_code,
            raw_payload=payload,
            snapshots=snapshots,
        )

    def _parse_pair(self, pair: dict, subject_mint: str) -> PoolSnapshot | None:
        pool_address = pair.get("pairAddress")
        base_mint = dig(pair, "baseToken", "address")
        if not pool_address or not base_mint:
            return None

        txns = pair.get("txns") if isinstance(pair.get("txns"), dict) else {}
        vol = pair.get("volume") if isinstance(pair.get("volume"), dict) else {}
        chg = pair.get("priceChange") if isinstance(pair.get("priceChange"), dict) else {}

        h24 = txns.get("h24") if isinstance(txns.get("h24"), dict) else {}
        h1 = txns.get("h1") if isinstance(txns.get("h1"), dict) else {}
        buys_24, sells_24 = to_int(h24.get("buys")), to_int(h24.get("sells"))
        buys_1, sells_1 = to_int(h1.get("buys")), to_int(h1.get("sells"))

        base_name = dig(pair, "baseToken", "name")
        base_symbol = dig(pair, "baseToken", "symbol")

        snap = PoolSnapshot(
            pool_address=pool_address,
            base_token_mint=base_mint,
            network=pair.get("chainId") or self.network,
            quote_token_mint=dig(pair, "quoteToken", "address"),
            pool_name=(
                f"{base_symbol} / {dig(pair, 'quoteToken', 'symbol')}"
                if base_symbol
                else None
            ),
            base_token_symbol=base_symbol,
            base_token_name=base_name,
            dex=pair.get("dexId"),
            pool_created_at=to_utc_datetime(pair.get("pairCreatedAt")),
            observed_at=datetime.now(timezone.utc),
            price_usd=to_float(pair.get("priceUsd")),
            price_native=to_float(pair.get("priceNative")),
            liquidity_usd=to_float(dig(pair, "liquidity", "usd")),
            fdv_usd=to_float(pair.get("fdv")),
            market_cap_usd=to_float(pair.get("marketCap")),
            volume_usd_m5=to_float(vol.get("m5")),
            volume_usd_h1=to_float(vol.get("h1")),
            volume_usd_h6=to_float(vol.get("h6")),
            volume_usd_h24=to_float(vol.get("h24")),
            txns_h1=(buys_1 + sells_1) if buys_1 is not None and sells_1 is not None else None,
            txns_h24=(buys_24 + sells_24) if buys_24 is not None and sells_24 is not None else None,
            buys_h24=buys_24,
            sells_h24=sells_24,
            # DexScreener does not expose unique participant counts.
            unique_traders_h24=None,
            buyers_h24=None,
            sellers_h24=None,
            price_change_pct_h1=to_float(chg.get("h1")),
            price_change_pct_h24=to_float(chg.get("h24")),
        )
        snap.missing_fields = [f for f in _EXPECTED_FIELDS if getattr(snap, f, None) is None]
        return snap
