"""Normalized domain objects returned by providers.

Providers translate their own wire formats into these shapes, so the pipeline
and the database never depend on a single vendor's field names. Adding a
provider means implementing the translation, not changing the schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PoolSnapshot:
    """A pool's identity plus its market state at one instant."""

    # --- identity ---
    pool_address: str
    base_token_mint: str
    network: str = "solana"
    quote_token_mint: str | None = None
    pool_name: str | None = None
    base_token_symbol: str | None = None
    base_token_name: str | None = None
    dex: str | None = None
    pool_created_at: datetime | None = None

    # --- market state ---
    observed_at: datetime | None = None
    price_usd: float | None = None
    price_native: float | None = None
    liquidity_usd: float | None = None
    fdv_usd: float | None = None
    market_cap_usd: float | None = None

    volume_usd_m5: float | None = None
    volume_usd_h1: float | None = None
    volume_usd_h6: float | None = None
    volume_usd_h24: float | None = None

    txns_h1: int | None = None
    txns_h24: int | None = None
    buys_h24: int | None = None
    sells_h24: int | None = None
    unique_traders_h24: int | None = None
    buyers_h24: int | None = None
    sellers_h24: int | None = None

    price_change_pct_h1: float | None = None
    price_change_pct_h24: float | None = None

    #: fields that the provider returned as null/absent
    missing_fields: list[str] = field(default_factory=list)

    @property
    def missing_field_count(self) -> int:
        return len(self.missing_fields)


@dataclass
class Candle:
    """One OHLCV bar."""

    ts: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume_usd: float | None


@dataclass
class HolderStats:
    """Holder concentration for a mint. Not populated in the MVP."""

    mint: str
    observed_at: datetime
    holder_count: int | None = None
    top1_share: float | None = None
    top10_share: float | None = None
    herfindahl_index: float | None = None
    total_supply: float | None = None


@dataclass
class FetchResult:
    """A provider call's normalized output plus the raw payload for provenance."""

    endpoint: str
    params: dict[str, Any]
    http_status: int
    raw_payload: Any
    snapshots: list[PoolSnapshot] = field(default_factory=list)
    candles: list[Candle] = field(default_factory=list)
