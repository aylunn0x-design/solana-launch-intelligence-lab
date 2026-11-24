"""Provider registry: how sources are declared and swapped.

The pipeline asks the registry for a provider by role, never by constructing a
vendor client directly. Replacing a data source means registering a different
implementation here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import DataSource
from app.providers.dexscreener import BASE_URL as DEX_BASE, SOURCE_NAME as DEX_NAME
from app.providers.dexscreener import DexScreenerProvider
from app.providers.geckoterminal import BASE_URL as GT_BASE, SOURCE_NAME as GT_NAME
from app.providers.geckoterminal import GeckoTerminalProvider
from app.providers.solana_rpc import SOURCE_NAME as RPC_NAME, SolanaRpcProvider


@dataclass(frozen=True)
class SourceSpec:
    """Static description of a source, mirrored into the data_sources table."""

    name: str
    base_url: str
    description: str
    requires_api_key: bool
    enabled_by_default: bool


SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        name=GT_NAME,
        base_url=GT_BASE,
        description=(
            "GeckoTerminal public API. Primary source: new-pool discovery with "
            "pool_created_at, per-window volume, unique trader counts, and OHLCV history."
        ),
        requires_api_key=False,
        enabled_by_default=True,
    ),
    SourceSpec(
        name=DEX_NAME,
        base_url=DEX_BASE,
        description=(
            "DexScreener API. Secondary source used for independent cross-validation "
            "of liquidity and volume. Does not report unique traders."
        ),
        requires_api_key=False,
        enabled_by_default=True,
    ),
    SourceSpec(
        name=RPC_NAME,
        base_url="https://api.mainnet-beta.solana.com",
        description=(
            "Solana public JSON-RPC. Holder concentration only. DEGRADED: the public "
            "endpoint returns HTTP 429 for getTokenLargestAccounts, so this source is "
            "disabled by default and holder metrics are reported as unavailable."
        ),
        requires_api_key=False,
        enabled_by_default=False,
    ),
)


def sync_sources(session: Session) -> dict[str, int]:
    """Ensure every declared source exists in the DB. Returns name -> id.

    Idempotent: safe to call on every startup and from migrations/seed scripts.
    """
    ids: dict[str, int] = {}
    existing = {s.name: s for s in session.query(DataSource).all()}

    for spec in SOURCE_SPECS:
        row = existing.get(spec.name)
        if row is None:
            row = DataSource(
                name=spec.name,
                base_url=spec.base_url,
                description=spec.description,
                requires_api_key=spec.requires_api_key,
                is_enabled=spec.enabled_by_default,
            )
            session.add(row)
            session.flush()
        else:
            # Keep descriptive metadata current without clobbering the operator's
            # enable/disable choice.
            row.base_url = spec.base_url
            row.description = spec.description
            row.requires_api_key = spec.requires_api_key
        ids[spec.name] = row.id

    session.commit()
    return ids


def get_source_id(session: Session, name: str) -> int:
    row = session.query(DataSource).filter(DataSource.name == name).one_or_none()
    if row is None:
        return sync_sources(session)[name]
    return row.id


# --- provider factories -----------------------------------------------------


def build_primary_provider(settings: Settings | None = None) -> GeckoTerminalProvider:
    s = settings or get_settings()
    return GeckoTerminalProvider.build(
        rate_per_min=s.geckoterminal_rate_limit_per_min,
        timeout=s.http_timeout_seconds,
        max_retries=s.http_max_retries,
    )


def build_validation_provider(settings: Settings | None = None) -> DexScreenerProvider:
    s = settings or get_settings()
    return DexScreenerProvider.build(
        rate_per_min=s.dexscreener_rate_limit_per_min,
        timeout=s.http_timeout_seconds,
        max_retries=s.http_max_retries,
    )


def build_holder_provider(settings: Settings | None = None) -> SolanaRpcProvider:
    s = settings or get_settings()
    return SolanaRpcProvider.build(
        rate_per_min=s.solana_rpc_rate_limit_per_min,
        rpc_url=s.solana_rpc_url,
        timeout=s.http_timeout_seconds,
    )
