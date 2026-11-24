"""GeckoTerminal provider — the primary data source.

Verified live against https://api.geckoterminal.com/api/v2 (no API key):
  * GET /networks/{network}/new_pools          -> 20 pools/page, with pool_created_at
  * GET /networks/{network}/pools              -> top pools (survivorship-biased)
  * GET /networks/{network}/trending_pools     -> trending pools (survivorship-biased)
  * GET /networks/{network}/pools/{pool}/ohlcv/{timeframe}?limit=1000 -> 1000 candles

Field names and nesting below were read from actual responses, not from
documentation. See DATA_SOURCES.md for the inspected payload shape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.providers.base import ProviderError, RateLimitedClient
from app.providers.parsing import dig, strip_network_prefix, to_float, to_int, to_utc_datetime
from app.providers.types import Candle, FetchResult, PoolSnapshot

logger = logging.getLogger(__name__)

SOURCE_NAME = "geckoterminal"
BASE_URL = "https://api.geckoterminal.com/api/v2"

#: Maximum candles the endpoint returned in testing.
MAX_OHLCV_LIMIT = 1000

#: The new_pools feed stops at page 10; page 11 returns HTTP 401
#: ("Your request exceeds the allowed max number of pages"). Verified live.
#: Consequence: one full sweep sees only the ~6 most recent minutes of launches
#: (20 pools/page x 10 pages). The unbiased inception cohort therefore has to be
#: accumulated by polling on a schedule — it cannot be backfilled.
MAX_NEW_POOLS_PAGE = 10

#: Fields we expect on a pool snapshot; anything absent is recorded as missing
#: rather than defaulted, because "no data" and "zero" are different facts.
_EXPECTED_FIELDS = (
    "liquidity_usd",
    "price_usd",
    "fdv_usd",
    "volume_usd_h24",
    "txns_h24",
    "unique_traders_h24",
)


class GeckoTerminalProvider:
    """Translates GeckoTerminal responses into normalized domain objects."""

    name = SOURCE_NAME
    base_url = BASE_URL
    requires_api_key = False

    def __init__(self, client: RateLimitedClient, network: str = "solana"):
        self.client = client
        self.network = network

    # -- construction -------------------------------------------------------

    @classmethod
    def build(cls, *, rate_per_min: int, timeout: float = 25.0, max_retries: int = 4,
              network: str = "solana") -> "GeckoTerminalProvider":
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

    # -- discovery ----------------------------------------------------------

    async def fetch_new_pools(self, page: int = 1) -> FetchResult:
        """Newly created pools: the unbiased inception cohort.

        Raises ValueError beyond MAX_NEW_POOLS_PAGE rather than letting the
        provider answer 401, so callers get a clear reason.
        """
        if page > MAX_NEW_POOLS_PAGE:
            raise ValueError(
                f"new_pools supports pages 1..{MAX_NEW_POOLS_PAGE}; got {page}"
            )
        path = f"/networks/{self.network}/new_pools"
        resp = await self.client.get_json(path, {"page": page})
        snapshots = self._parse_pool_list(resp.payload)
        return FetchResult(
            endpoint=path,
            params={"page": page},
            http_status=resp.status_code,
            raw_payload=resp.payload,
            snapshots=snapshots,
        )

    async def fetch_top_pools(self, page: int = 1) -> FetchResult:
        """Currently-liquid pools. Survivorship-biased by construction."""
        path = f"/networks/{self.network}/pools"
        resp = await self.client.get_json(path, {"page": page})
        return FetchResult(
            endpoint=path,
            params={"page": page},
            http_status=resp.status_code,
            raw_payload=resp.payload,
            snapshots=self._parse_pool_list(resp.payload),
        )

    async def fetch_trending_pools(self, page: int = 1) -> FetchResult:
        path = f"/networks/{self.network}/trending_pools"
        resp = await self.client.get_json(path, {"page": page})
        return FetchResult(
            endpoint=path,
            params={"page": page},
            http_status=resp.status_code,
            raw_payload=resp.payload,
            snapshots=self._parse_pool_list(resp.payload),
        )

    async def fetch_pool(self, pool_address: str) -> FetchResult:
        """Re-poll a single known pool, to extend its longitudinal panel."""
        path = f"/networks/{self.network}/pools/{pool_address}"
        resp = await self.client.get_json(path)
        payload = resp.payload
        data = payload.get("data") if isinstance(payload, dict) else None
        snapshots = []
        if isinstance(data, dict):
            parsed = self._parse_pool(data)
            if parsed:
                snapshots.append(parsed)
        elif isinstance(data, list):
            snapshots = self._parse_pool_list(payload)
        return FetchResult(
            endpoint=path,
            params={},
            http_status=resp.status_code,
            raw_payload=payload,
            snapshots=snapshots,
        )

    # -- history ------------------------------------------------------------

    async def fetch_ohlcv(
        self,
        pool_address: str,
        timeframe: str = "hour",
        aggregate: int = 1,
        limit: int = MAX_OHLCV_LIMIT,
        before_timestamp: int | None = None,
    ) -> FetchResult:
        """Historical OHLCV for a pool.

        `before_timestamp` pages further back in history (each call returns at
        most MAX_OHLCV_LIMIT bars, newest first).
        """
        if timeframe not in {"minute", "hour", "day"}:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        path = f"/networks/{self.network}/pools/{pool_address}/ohlcv/{timeframe}"
        params: dict[str, int | str] = {
            "aggregate": aggregate,
            "limit": min(limit, MAX_OHLCV_LIMIT),
        }
        if before_timestamp is not None:
            params["before_timestamp"] = before_timestamp

        resp = await self.client.get_json(path, params)
        return FetchResult(
            endpoint=path,
            params=dict(params),
            http_status=resp.status_code,
            raw_payload=resp.payload,
            candles=self._parse_ohlcv(resp.payload),
        )

    # -- parsing ------------------------------------------------------------

    def _parse_pool_list(self, payload: object) -> list[PoolSnapshot]:
        if not isinstance(payload, dict):
            raise ProviderError("expected a JSON object at the top level")
        data = payload.get("data")
        if data is None:
            raise ProviderError("response has no 'data' key")
        if not isinstance(data, list):
            raise ProviderError(f"'data' was {type(data).__name__}, expected list")

        out: list[PoolSnapshot] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            parsed = self._parse_pool(item)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_pool(self, item: dict) -> PoolSnapshot | None:
        """Translate one pool object. Returns None if it lacks an identity.

        A pool with no address or no base-token mint cannot be stored or joined,
        so it is dropped and counted as skipped rather than stored half-formed.
        """
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            return None

        pool_address = attrs.get("address") or strip_network_prefix(
            item.get("id"), self.network
        )
        base_mint = strip_network_prefix(
            dig(item, "relationships", "base_token", "data", "id"), self.network
        )
        if not pool_address or not base_mint:
            return None

        quote_mint = strip_network_prefix(
            dig(item, "relationships", "quote_token", "data", "id"), self.network
        )
        dex = dig(item, "relationships", "dex", "data", "id")

        txns = attrs.get("transactions") if isinstance(attrs.get("transactions"), dict) else {}
        vol = attrs.get("volume_usd") if isinstance(attrs.get("volume_usd"), dict) else {}
        chg = (
            attrs.get("price_change_percentage")
            if isinstance(attrs.get("price_change_percentage"), dict)
            else {}
        )

        h24 = txns.get("h24") if isinstance(txns.get("h24"), dict) else {}
        h1 = txns.get("h1") if isinstance(txns.get("h1"), dict) else {}

        buys_24 = to_int(h24.get("buys"))
        sells_24 = to_int(h24.get("sells"))
        buys_1 = to_int(h1.get("buys"))
        sells_1 = to_int(h1.get("sells"))

        snap = PoolSnapshot(
            pool_address=pool_address,
            base_token_mint=base_mint,
            network=self.network,
            quote_token_mint=quote_mint,
            pool_name=attrs.get("name"),
            base_token_symbol=self._symbol_from_pool_name(attrs.get("name")),
            dex=dex if isinstance(dex, str) else None,
            pool_created_at=to_utc_datetime(attrs.get("pool_created_at")),
            observed_at=datetime.now(timezone.utc),
            price_usd=to_float(attrs.get("base_token_price_usd")),
            price_native=to_float(attrs.get("base_token_price_native_currency")),
            liquidity_usd=to_float(attrs.get("reserve_in_usd")),
            fdv_usd=to_float(attrs.get("fdv_usd")),
            market_cap_usd=to_float(attrs.get("market_cap_usd")),
            volume_usd_m5=to_float(vol.get("m5")),
            volume_usd_h1=to_float(vol.get("h1")),
            volume_usd_h6=to_float(vol.get("h6")),
            volume_usd_h24=to_float(vol.get("h24")),
            txns_h1=(buys_1 + sells_1) if buys_1 is not None and sells_1 is not None else None,
            txns_h24=(buys_24 + sells_24) if buys_24 is not None and sells_24 is not None else None,
            buys_h24=buys_24,
            sells_h24=sells_24,
            unique_traders_h24=to_int(h24.get("unique_traders")),
            buyers_h24=to_int(h24.get("buyers")),
            sellers_h24=to_int(h24.get("sellers")),
            price_change_pct_h1=to_float(chg.get("h1")),
            price_change_pct_h24=to_float(chg.get("h24")),
        )
        snap.missing_fields = [f for f in _EXPECTED_FIELDS if getattr(snap, f, None) is None]
        return snap

    @staticmethod
    def _symbol_from_pool_name(pool_name: object) -> str | None:
        """Pool names look like "TOKEN / SOL"; the base symbol is the left side.

        This is the only symbol GeckoTerminal's pool payload carries. It is a
        convenience label only — the mint address is the identifier used for
        joins, so a mis-split name cannot corrupt the dataset.
        """
        if not isinstance(pool_name, str) or "/" not in pool_name:
            return pool_name.strip() if isinstance(pool_name, str) and pool_name.strip() else None
        return pool_name.split("/")[0].strip() or None

    @staticmethod
    def _parse_ohlcv(payload: object) -> list[Candle]:
        """Parse data.attributes.ohlcv_list -> [[ts, o, h, l, c, volume], ...]."""
        if not isinstance(payload, dict):
            raise ProviderError("OHLCV response was not a JSON object")
        rows = dig(payload, "data", "attributes", "ohlcv_list", default=None)
        if rows is None:
            raise ProviderError("OHLCV response missing data.attributes.ohlcv_list")
        if not isinstance(rows, list):
            raise ProviderError("ohlcv_list was not a list")

        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            ts = to_utc_datetime(row[0])
            if ts is None:
                continue
            candles.append(
                Candle(
                    ts=ts,
                    open=to_float(row[1]),
                    high=to_float(row[2]),
                    low=to_float(row[3]),
                    close=to_float(row[4]),
                    volume_usd=to_float(row[5]),
                )
            )
        # Provider returns newest-first; ascending order is easier to reason about.
        candles.sort(key=lambda c: c.ts)
        return candles
