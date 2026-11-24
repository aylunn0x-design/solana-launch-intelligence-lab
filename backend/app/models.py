"""SQLAlchemy ORM models — the analytical schema.

Design notes
------------
* The unit of observation is a **pool** (an AMM market for a token), not a token.
  A token can have several pools; "launch" is the creation of a pool. GeckoTerminal
  reports `pool_created_at`, which is the launch clock used throughout the study.
* Raw provider payloads are preserved in `raw_payloads` so every derived number can
  be recomputed from the original bytes. This is what makes results reproducible.
* Every observation carries `source_id` so results are traceable to a provider.
* `feature_definitions.availability` is the structural anti-leakage control: a
  feature is declared as known-at-launch, forward-looking, or an outcome, and the
  research engine refuses to train on anything that is not known-at-launch.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DiscoveryMethod(str, enum.Enum):
    """How a pool entered the dataset. This determines its bias properties.

    NEW_POOLS_FEED  - captured from the "new pools" feed at (or near) creation.
                      Every launch is seen regardless of what happens to it
                      later, so this cohort is free of survivorship bias and is
                      the default population for inferential work.
    TOP_POOLS       - found in a ranked list of currently-liquid pools.
    TRENDING_POOLS  - found in a trending list.
    MANUAL          - explicitly requested by mint/pool address.

    TOP_POOLS, TRENDING_POOLS and MANUAL are survivorship-biased: a pool only
    appears because it is *currently* notable. Analyses must not mix these with
    the inception cohort without saying so.
    """

    NEW_POOLS_FEED = "new_pools_feed"
    TOP_POOLS = "top_pools"
    TRENDING_POOLS = "trending_pools"
    MANUAL = "manual"

    @property
    def is_survivorship_biased(self) -> bool:
        return self is not DiscoveryMethod.NEW_POOLS_FEED


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class FeatureAvailability(str, enum.Enum):
    """When a feature's value becomes knowable. The anti-leakage contract.

    AT_LAUNCH       - computable from information available at/just after pool
                      creation. Safe to use as a model input.
    FORWARD_LOOKING - uses data after launch. Descriptive only; using it to
                      predict an outcome measured over the same period leaks.
    OUTCOME         - a dependent variable. Never a model input.
    """

    AT_LAUNCH = "at_launch"
    FORWARD_LOOKING = "forward_looking"
    OUTCOME = "outcome"


class Timeframe(str, enum.Enum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class DataSource(Base):
    """A provider of data. Anchors provenance for every observation."""

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    observations: Mapped[list["MarketObservation"]] = relationship(back_populates="source")


class IngestionJob(Base):
    """One run of one pipeline stage. Every write is attributable to a job."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=16), default=JobStatus.PENDING, nullable=False
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    params: Mapped[dict | None] = mapped_column(JSON)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    records_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_requests_made: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_limit_hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_message: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_jobs_type_status", "job_type", "status"),)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class RawPayload(Base):
    """An unmodified provider response.

    Kept so that every normalized value can be re-derived from the original
    bytes. This is the difference between "a number in a database" and
    reproducible research.
    """

    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="SET NULL"))
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    request_params: Mapped[dict | None] = mapped_column(JSON)
    http_status: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict | list | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    __table_args__ = (Index("ix_raw_source_endpoint", "source_id", "endpoint"),)


# ---------------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------------


class Token(Base):
    """An SPL token, keyed by mint address."""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    mint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    pools: Mapped[list["Pool"]] = relationship(
        back_populates="token", cascade="all, delete-orphan"
    )
    holder_snapshots: Mapped[list["HolderSnapshot"]] = relationship(
        back_populates="token", cascade="all, delete-orphan"
    )


class Pool(Base):
    """An AMM pool / trading pair. The unit of a "launch"."""

    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(primary_key=True)
    pool_address: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    token_id: Mapped[int] = mapped_column(
        ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False, index=True
    )

    network: Mapped[str] = mapped_column(String(32), default="solana", nullable=False)
    dex: Mapped[str | None] = mapped_column(String(64), index=True)
    pool_name: Mapped[str | None] = mapped_column(String(255))
    quote_token_mint: Mapped[str | None] = mapped_column(String(64))

    # The launch clock. Nullable because a provider may not report it; rows
    # without it are excluded from launch-cohort analysis rather than guessed.
    pool_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    discovery_method: Mapped[DiscoveryMethod] = mapped_column(
        Enum(DiscoveryMethod, native_enum=False, length=32), nullable=False, index=True
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    token: Mapped["Token"] = relationship(back_populates="pools")
    observations: Mapped[list["MarketObservation"]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )
    candles: Mapped[list["OhlcvCandle"]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_pools_created_discovery", "pool_created_at", "discovery_method"),
    )


class MarketObservation(Base):
    """A point-in-time snapshot of a pool's market state, as reported by a source.

    One row per (pool, source, observation time). Repeated polling of the same
    pool builds a longitudinal panel. Storing per-source rows (rather than
    merging) is what allows cross-source disagreement to be measured.
    """

    __tablename__ = "market_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="SET NULL"))
    raw_payload_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_payloads.id", ondelete="SET NULL")
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # --- Liquidity / valuation ---
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    fdv_usd: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    price_native: Mapped[float | None] = mapped_column(Float)

    # --- Activity, per window. Nullable: not every source reports every window.
    volume_usd_m5: Mapped[float | None] = mapped_column(Float)
    volume_usd_h1: Mapped[float | None] = mapped_column(Float)
    volume_usd_h6: Mapped[float | None] = mapped_column(Float)
    volume_usd_h24: Mapped[float | None] = mapped_column(Float)

    txns_h1: Mapped[int | None] = mapped_column(Integer)
    txns_h24: Mapped[int | None] = mapped_column(Integer)
    buys_h24: Mapped[int | None] = mapped_column(Integer)
    sells_h24: Mapped[int | None] = mapped_column(Integer)

    # Unique participant counts. GeckoTerminal reports these; DexScreener does not.
    unique_traders_h24: Mapped[int | None] = mapped_column(Integer)
    buyers_h24: Mapped[int | None] = mapped_column(Integer)
    sellers_h24: Mapped[int | None] = mapped_column(Integer)

    price_change_pct_h1: Mapped[float | None] = mapped_column(Float)
    price_change_pct_h24: Mapped[float | None] = mapped_column(Float)

    # Count of expected fields that arrived null, for data-quality reporting.
    missing_field_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pool: Mapped["Pool"] = relationship(back_populates="observations")
    source: Mapped["DataSource"] = relationship(back_populates="observations")

    __table_args__ = (
        # Deduplication contract: the same source cannot record two states for
        # the same pool at the same instant.
        UniqueConstraint("pool_id", "source_id", "observed_at", name="uq_obs_pool_source_time"),
        Index("ix_obs_pool_time", "pool_id", "observed_at"),
        CheckConstraint("liquidity_usd IS NULL OR liquidity_usd >= 0", name="ck_obs_liq_nonneg"),
    )


class OhlcvCandle(Base):
    """A historical OHLCV candle for a pool.

    This is the retrospective history: it lets us reconstruct a pool's price and
    volume path since launch without having observed it live.
    """

    __tablename__ = "ohlcv_candles"

    id: Mapped[int] = mapped_column(primary_key=True)
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="SET NULL"))

    timeframe: Mapped[Timeframe] = mapped_column(
        Enum(Timeframe, native_enum=False, length=8), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume_usd: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pool: Mapped["Pool"] = relationship(back_populates="candles")

    __table_args__ = (
        UniqueConstraint("pool_id", "timeframe", "ts", name="uq_candle_pool_tf_ts"),
        Index("ix_candle_pool_tf_ts", "pool_id", "timeframe", "ts"),
        CheckConstraint("volume_usd IS NULL OR volume_usd >= 0", name="ck_candle_vol_nonneg"),
    )


class HolderSnapshot(Base):
    """Holder-concentration snapshot.

    NOTE: not populated in the MVP. The public Solana RPC returns HTTP 429 for
    getTokenLargestAccounts, so these metrics cannot be collected reliably
    without a dedicated RPC provider. The table exists so the capability can be
    switched on later; the API reports holder data as unavailable rather than
    inventing it. See DATA_SOURCES.md section 4.
    """

    __tablename__ = "holder_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(
        ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    holder_count: Mapped[int | None] = mapped_column(Integer)
    top1_share: Mapped[float | None] = mapped_column(Float)
    top10_share: Mapped[float | None] = mapped_column(Float)
    herfindahl_index: Mapped[float | None] = mapped_column(Float)
    total_supply: Mapped[float | None] = mapped_column(Float)

    token: Mapped["Token"] = relationship(back_populates="holder_snapshots")

    __table_args__ = (
        UniqueConstraint("token_id", "source_id", "observed_at", name="uq_holder_token_src_time"),
    )


# ---------------------------------------------------------------------------
# Feature store and dataset versioning
# ---------------------------------------------------------------------------


class FeatureDefinition(Base):
    """A documented, versioned feature. The anti-leakage registry."""

    __tablename__ = "feature_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    dtype: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    availability: Mapped[FeatureAvailability] = mapped_column(
        Enum(FeatureAvailability, native_enum=False, length=20), nullable=False, index=True
    )
    window: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("name", "version", name="uq_feature_name_version"),)


class DatasetVersion(Base):
    """An immutable, named analytical dataset.

    Experiments reference a dataset version so a result can be reproduced against
    exactly the rows it was computed from.
    """

    __tablename__ = "dataset_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Inclusion/exclusion criteria actually applied, recorded verbatim.
    criteria: Mapped[dict | None] = mapped_column(JSON)
    inactivity_threshold_hours: Mapped[int] = mapped_column(Integer, nullable=False)

    pool_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Share of pools from the unbiased inception cohort, for honest reporting.
    inception_cohort_share: Mapped[float | None] = mapped_column(Float)

    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    features: Mapped[list["PoolFeature"]] = relationship(
        back_populates="dataset_version", cascade="all, delete-orphan"
    )


class PoolFeature(Base):
    """Computed feature row for one pool within one dataset version.

    Stored (not recomputed on the fly) so that an experiment's inputs are frozen
    alongside its dataset version.
    """

    __tablename__ = "pool_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("pools.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- Known at launch (safe as model inputs) ---
    initial_liquidity_usd: Mapped[float | None] = mapped_column(Float)
    initial_fdv_usd: Mapped[float | None] = mapped_column(Float)
    launch_hour_utc: Mapped[int | None] = mapped_column(Integer)
    launch_dow: Mapped[int | None] = mapped_column(Integer)
    dex: Mapped[str | None] = mapped_column(String(64))
    first_hour_volume_usd: Mapped[float | None] = mapped_column(Float)
    first_hour_unique_traders: Mapped[int | None] = mapped_column(Integer)
    first_hour_txns: Mapped[int | None] = mapped_column(Integer)

    # --- Forward-looking / descriptive ---
    peak_liquidity_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_cv: Mapped[float | None] = mapped_column(Float)
    total_volume_usd: Mapped[float | None] = mapped_column(Float)
    active_hours: Mapped[int | None] = mapped_column(Integer)
    activity_persistence: Mapped[float | None] = mapped_column(Float)
    realized_volatility: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)

    # --- Outcomes (never model inputs) ---
    duration_hours: Mapped[float | None] = mapped_column(Float)
    event_observed: Mapped[bool | None] = mapped_column(Boolean)
    survived_24h: Mapped[bool | None] = mapped_column(Boolean)
    survived_72h: Mapped[bool | None] = mapped_column(Boolean)

    # --- Data quality ---
    candle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completeness: Mapped[float | None] = mapped_column(Float)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    dataset_version: Mapped["DatasetVersion"] = relationship(back_populates="features")
    pool: Mapped["Pool"] = relationship()

    __table_args__ = (
        UniqueConstraint("dataset_version_id", "pool_id", name="uq_feature_dsv_pool"),
    )


# ---------------------------------------------------------------------------
# Experiment tracking
# ---------------------------------------------------------------------------


class ResearchExperiment(Base):
    __tablename__ = "research_experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)

    dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL")
    )
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    features_used: Mapped[list | None] = mapped_column(JSON)
    target_definition: Mapped[str | None] = mapped_column(Text)
    split_strategy: Mapped[str | None] = mapped_column(String(64))
    parameters: Mapped[dict | None] = mapped_column(JSON)

    notes: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    results: Mapped[list["ExperimentResult"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    dataset_version: Mapped["DatasetVersion | None"] = relationship()


class ExperimentResult(Base):
    """A metric produced by an experiment.

    One row per metric so results are queryable and comparable across
    experiments, rather than buried in a JSON blob.
    """

    __tablename__ = "experiment_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float | None] = mapped_column(Float)
    metric_text: Mapped[str | None] = mapped_column(Text)

    sample_size: Mapped[int | None] = mapped_column(Integer)
    ci_lower: Mapped[float | None] = mapped_column(Float)
    ci_upper: Mapped[float | None] = mapped_column(Float)
    p_value: Mapped[float | None] = mapped_column(Float)
    split: Mapped[str | None] = mapped_column(String(32))

    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    experiment: Mapped["ResearchExperiment"] = relationship(back_populates="results")

    __table_args__ = (Index("ix_result_exp_metric", "experiment_id", "metric_name"),)
