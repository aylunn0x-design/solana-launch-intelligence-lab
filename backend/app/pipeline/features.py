"""Feature engineering and dataset versioning.

Every feature is a documented, testable function of stored observations. Features
are computed into `pool_features` rows attached to an immutable
`DatasetVersion`, so an experiment's inputs are frozen alongside its results.

The leakage boundary
--------------------
Features are declared in `FEATURE_REGISTRY` with an `availability`:

  AT_LAUNCH       computable from information available at/just after pool
                  creation -> legal as a model input
  FORWARD_LOOKING uses data from after launch -> descriptive only
  OUTCOME         a dependent variable -> never a model input

`model_input_features()` is the single source of truth for what a model may
train on, and `assert_no_leakage()` raises if anything else is passed. This is
enforced in code and covered by tests, rather than left to reviewer discipline.

Survival outcome definition
---------------------------
"Death" is the end of *measurable trading activity*, which is what on-chain data
can actually observe:

  last_activity = latest hourly candle with volume > 0
  if (now - last_activity) > inactivity_threshold  -> event observed (died)
      duration = last_activity - pool_created_at
  else                                             -> right-censored (alive)
      duration = now - pool_created_at

The threshold is a parameter of the dataset version, not a constant, because
RQ6 asks how sensitive conclusions are to this choice.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    DatasetVersion,
    DiscoveryMethod,
    FeatureAvailability,
    FeatureDefinition,
    MarketObservation,
    OhlcvCandle,
    Pool,
    PoolFeature,
    Timeframe,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature registry — the documented contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str
    availability: FeatureAvailability
    description: str
    unit: str | None = None
    window: str | None = None


FEATURE_REGISTRY: tuple[FeatureSpec, ...] = (
    # --- known at launch: legal model inputs ---
    FeatureSpec(
        "initial_liquidity_usd", "float", FeatureAvailability.AT_LAUNCH,
        "Pool reserve in USD at the earliest observation we hold for the pool. "
        "Proxy for launch liquidity; exact only if the pool was observed at creation.",
        unit="USD",
    ),
    FeatureSpec(
        "initial_fdv_usd", "float", FeatureAvailability.AT_LAUNCH,
        "Fully diluted valuation at the earliest observation.", unit="USD",
    ),
    FeatureSpec(
        "launch_hour_utc", "int", FeatureAvailability.AT_LAUNCH,
        "UTC hour (0-23) of pool creation. Tests whether launch timing matters.",
    ),
    FeatureSpec(
        "launch_dow", "int", FeatureAvailability.AT_LAUNCH,
        "Day of week of pool creation (Monday=0).",
    ),
    FeatureSpec(
        "dex", "categorical", FeatureAvailability.AT_LAUNCH,
        "DEX/AMM hosting the pool, e.g. pumpswap, pump-fun, orca, raydium.",
    ),
    FeatureSpec(
        "first_hour_volume_usd", "float", FeatureAvailability.AT_LAUNCH,
        "Traded volume in the first hourly candle at/after pool creation.",
        unit="USD", window="first 1h",
    ),
    FeatureSpec(
        "first_hour_unique_traders", "int", FeatureAvailability.AT_LAUNCH,
        "Unique traders reported in the earliest observation's 24h window. "
        "Only meaningful when the pool was observed within its first hour; "
        "otherwise null.",
        window="first 1h",
    ),
    FeatureSpec(
        "first_hour_txns", "int", FeatureAvailability.AT_LAUNCH,
        "Transaction count in the earliest observation's 24h window, subject to "
        "the same caveat as first_hour_unique_traders.",
        window="first 1h",
    ),
    # --- forward looking: descriptive only ---
    FeatureSpec(
        "peak_liquidity_usd", "float", FeatureAvailability.FORWARD_LOOKING,
        "Maximum observed pool liquidity across all observations.", unit="USD",
    ),
    FeatureSpec(
        "liquidity_cv", "float", FeatureAvailability.FORWARD_LOOKING,
        "Coefficient of variation (sd/mean) of observed liquidity. Scale-free "
        "measure of liquidity instability. Requires >= 2 observations.",
    ),
    FeatureSpec(
        "total_volume_usd", "float", FeatureAvailability.FORWARD_LOOKING,
        "Sum of candle volume over the pool's observed history.", unit="USD",
    ),
    FeatureSpec(
        "active_hours", "int", FeatureAvailability.FORWARD_LOOKING,
        "Number of hourly candles with volume > 0.", unit="hours",
    ),
    FeatureSpec(
        "activity_persistence", "float", FeatureAvailability.FORWARD_LOOKING,
        "active_hours divided by hours elapsed between first and last candle. "
        "1.0 = traded every hour; near 0 = sporadic.",
    ),
    FeatureSpec(
        "realized_volatility", "float", FeatureAvailability.FORWARD_LOOKING,
        "Standard deviation of hourly log returns of the close price.",
    ),
    FeatureSpec(
        "max_drawdown_pct", "float", FeatureAvailability.FORWARD_LOOKING,
        "Largest peak-to-trough decline in close price, as a percentage.", unit="%",
    ),
    # --- outcomes ---
    FeatureSpec(
        "duration_hours", "float", FeatureAvailability.OUTCOME,
        "Survival time: hours from pool creation to last observed activity "
        "(if dead) or to now (if censored).", unit="hours",
    ),
    FeatureSpec(
        "event_observed", "bool", FeatureAvailability.OUTCOME,
        "True if the pool went inactive (event observed); False if right-censored.",
    ),
    FeatureSpec(
        "survived_24h", "bool", FeatureAvailability.OUTCOME,
        "Whether measurable activity continued at least 24h past creation. NULL when "
        "undetermined: a pool still active but younger than 24h has not yet had the "
        "opportunity to fail, so labelling it False would bias the outcome.",
    ),
    FeatureSpec(
        "survived_72h", "bool", FeatureAvailability.OUTCOME,
        "Whether measurable activity continued at least 72h past creation. NULL when "
        "undetermined (see survived_24h).",
    ),
)

FEATURE_BY_NAME = {f.name: f for f in FEATURE_REGISTRY}


def model_input_features() -> list[str]:
    """The only features a predictive model may use. Single source of truth."""
    return [f.name for f in FEATURE_REGISTRY if f.availability is FeatureAvailability.AT_LAUNCH]


def outcome_features() -> list[str]:
    return [f.name for f in FEATURE_REGISTRY if f.availability is FeatureAvailability.OUTCOME]


class LeakageError(ValueError):
    """Raised when a non-launch-time feature is used as a model input."""


def assert_no_leakage(feature_names: list[str]) -> None:
    """Fail loudly if any requested predictor is not known at launch."""
    legal = set(model_input_features())
    offenders: list[str] = []
    for name in feature_names:
        spec = FEATURE_BY_NAME.get(name)
        if spec is None:
            offenders.append(f"{name} (not in the feature registry)")
        elif name not in legal:
            offenders.append(f"{name} (availability={spec.availability.value})")
    if offenders:
        raise LeakageError(
            "These cannot be used as model inputs because their values are not "
            "known at launch time: " + "; ".join(offenders)
        )


def sync_feature_definitions(session: Session) -> int:
    """Mirror the registry into feature_definitions so the API can serve it."""
    existing = {
        (f.name, f.version) for f in session.execute(select(FeatureDefinition)).scalars()
    }
    added = 0
    for spec in FEATURE_REGISTRY:
        if (spec.name, 1) in existing:
            continue
        session.add(
            FeatureDefinition(
                name=spec.name,
                version=1,
                dtype=spec.dtype,
                unit=spec.unit,
                description=spec.description,
                availability=spec.availability,
                window=spec.window,
            )
        )
        added += 1
    session.commit()
    return added


# ---------------------------------------------------------------------------
# Pure computation helpers (unit-tested directly)
# ---------------------------------------------------------------------------


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def coefficient_of_variation(values: list[float]) -> float | None:
    """sd/mean. None when undefined (fewer than 2 points, or mean == 0)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var) / abs(mean)


def realized_volatility(closes: list[float]) -> float | None:
    """Standard deviation of log returns. None if fewer than 3 usable prices."""
    prices = [c for c in closes if c is not None and c > 0]
    if len(prices) < 3:
        return None
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def max_drawdown_pct(closes: list[float]) -> float | None:
    """Largest peak-to-trough decline, as a positive percentage."""
    prices = [c for c in closes if c is not None and c > 0]
    if len(prices) < 2:
        return None
    peak = prices[0]
    worst = 0.0
    for price in prices[1:]:
        peak = max(peak, price)
        if peak > 0:
            worst = max(worst, (peak - price) / peak)
    return worst * 100.0


def survived_at(
    duration_hours: float | None, event_observed: bool | None, horizon_hours: float
) -> bool | None:
    """Did the pool survive to `horizon_hours`? None when undetermined.

    This is the three-valued logic that keeps censoring from becoming bias:

      * observed for >= horizon              -> True  (it got there)
      * died before the horizon              -> False (it demonstrably failed)
      * still alive but younger than horizon -> None  (no verdict yet)

    Treating that last case as False would count every freshly-launched pool as
    a failure, which would make any "survival rate" a function of how recently
    we happened to start collecting data.
    """
    if duration_hours is None or event_observed is None:
        return None
    if duration_hours >= horizon_hours:
        return True
    return False if event_observed else None


@dataclass
class SurvivalOutcome:
    duration_hours: float | None
    event_observed: bool | None
    last_activity_at: datetime | None
    active_hours: int
    note: str = ""


def compute_survival(
    pool_created_at: datetime | None,
    candles: list[tuple[datetime, float | None]],
    *,
    inactivity_threshold_hours: int,
    now: datetime | None = None,
) -> SurvivalOutcome:
    """Derive survival time and censoring status from hourly candles.

    `candles` is [(timestamp, volume_usd), ...] in any order.
    """
    now = now or datetime.now(timezone.utc)
    created = _as_utc(pool_created_at)

    active = [(_as_utc(ts), v) for ts, v in candles if v is not None and v > 0]
    active = [(ts, v) for ts, v in active if ts is not None]
    active_hours = len(active)

    if created is None:
        return SurvivalOutcome(
            None, None, None, active_hours,
            "no pool_created_at: cannot place this pool on the launch clock",
        )

    if not active:
        return SurvivalOutcome(
            None, None, None, 0,
            "no candle with positive volume: no measurable activity to time",
        )

    last_activity = max(ts for ts, _ in active)
    hours_since_activity = (now - last_activity).total_seconds() / 3600.0

    if hours_since_activity > inactivity_threshold_hours:
        duration = (last_activity - created).total_seconds() / 3600.0
        # Guard against a provider reporting activity before the stated creation
        # time; such a pool is excluded rather than given a negative duration.
        if duration < 0:
            return SurvivalOutcome(
                None, None, last_activity, active_hours,
                "activity predates pool_created_at: inconsistent timestamps",
            )
        return SurvivalOutcome(duration, True, last_activity, active_hours)

    duration = (now - created).total_seconds() / 3600.0
    if duration < 0:
        return SurvivalOutcome(
            None, None, last_activity, active_hours, "pool_created_at is in the future"
        )
    return SurvivalOutcome(duration, False, last_activity, active_hours)


# ---------------------------------------------------------------------------
# Dataset construction
# ---------------------------------------------------------------------------

COHORT_FILTERS = {
    "all": None,
    "inception": DiscoveryMethod.NEW_POOLS_FEED,
    "retrospective": "not_inception",
}


def build_dataset_version(
    session: Session,
    *,
    label: str,
    cohort: str = "all",
    inactivity_threshold_hours: int = 24,
    min_candles: int = 6,
    description: str | None = None,
    now: datetime | None = None,
) -> DatasetVersion:
    """Compute features for every eligible pool into a new dataset version.

    Inclusion criteria are recorded on the version so the sample is auditable.
    """
    if cohort not in COHORT_FILTERS:
        raise ValueError(f"unknown cohort {cohort!r}; expected {sorted(COHORT_FILTERS)}")

    now = now or datetime.now(timezone.utc)
    sync_feature_definitions(session)

    if session.execute(
        select(DatasetVersion).where(DatasetVersion.label == label)
    ).scalar_one_or_none():
        raise ValueError(
            f"dataset version {label!r} already exists; versions are immutable, "
            "so choose a new label"
        )

    criteria = {
        "cohort": cohort,
        "min_candles": min_candles,
        "inactivity_threshold_hours": inactivity_threshold_hours,
        "requires_pool_created_at": True,
        "built_at": now.isoformat(),
        "notes": (
            "Pools without pool_created_at are excluded: they cannot be placed on "
            "the launch clock. Pools with fewer than min_candles hourly candles are "
            "excluded as insufficiently observed."
        ),
    }

    version = DatasetVersion(
        label=label,
        description=description,
        criteria=criteria,
        inactivity_threshold_hours=inactivity_threshold_hours,
    )
    session.add(version)
    session.flush()

    stmt = select(Pool).where(Pool.pool_created_at.is_not(None))
    if cohort == "inception":
        stmt = stmt.where(Pool.discovery_method == DiscoveryMethod.NEW_POOLS_FEED)
    elif cohort == "retrospective":
        stmt = stmt.where(Pool.discovery_method != DiscoveryMethod.NEW_POOLS_FEED)

    pools = list(session.execute(stmt).scalars())
    included = 0
    inception_count = 0
    total_obs = 0
    total_candles = 0

    for pool in pools:
        candle_rows = list(
            session.execute(
                select(OhlcvCandle.ts, OhlcvCandle.volume_usd, OhlcvCandle.close)
                .where(
                    OhlcvCandle.pool_id == pool.id,
                    OhlcvCandle.timeframe == Timeframe.HOUR,
                )
                .order_by(OhlcvCandle.ts)
            ).all()
        )
        if len(candle_rows) < min_candles:
            continue

        obs_rows = list(
            session.execute(
                select(
                    MarketObservation.observed_at,
                    MarketObservation.liquidity_usd,
                    MarketObservation.fdv_usd,
                    MarketObservation.unique_traders_h24,
                    MarketObservation.txns_h24,
                    MarketObservation.missing_field_count,
                )
                .where(MarketObservation.pool_id == pool.id)
                .order_by(MarketObservation.observed_at)
            ).all()
        )

        feature_row = _compute_pool_features(
            pool=pool,
            candle_rows=candle_rows,
            obs_rows=obs_rows,
            inactivity_threshold_hours=inactivity_threshold_hours,
            now=now,
        )
        if feature_row is None:
            continue

        feature_row.dataset_version_id = version.id
        session.add(feature_row)

        included += 1
        total_obs += len(obs_rows)
        total_candles += len(candle_rows)
        if pool.discovery_method is DiscoveryMethod.NEW_POOLS_FEED:
            inception_count += 1

    version.pool_count = included
    version.observation_count = total_obs
    version.candle_count = total_candles
    version.inception_cohort_share = (inception_count / included) if included else None

    session.commit()
    logger.info(
        "Built dataset %s: %d pools (%.0f%% inception cohort)",
        label, included, 100 * (version.inception_cohort_share or 0),
    )
    return version


def _compute_pool_features(
    *,
    pool: Pool,
    candle_rows: list[tuple],
    obs_rows: list[tuple],
    inactivity_threshold_hours: int,
    now: datetime,
) -> PoolFeature | None:
    """Compute one pool's feature row. Returns None if it cannot be timed."""
    created = _as_utc(pool.pool_created_at)

    volumes = [(ts, vol) for ts, vol, _ in candle_rows]
    closes = [c for _, _, c in candle_rows]

    survival = compute_survival(
        created, volumes, inactivity_threshold_hours=inactivity_threshold_hours, now=now
    )
    if survival.duration_hours is None:
        # Cannot place the pool on the launch clock -> excluded, not guessed.
        logger.debug("Excluding %s: %s", pool.pool_address, survival.note)
        return None

    # --- at-launch features -------------------------------------------------
    first_obs = obs_rows[0] if obs_rows else None
    initial_liquidity = first_obs[1] if first_obs else None
    initial_fdv = first_obs[2] if first_obs else None

    # Unique-trader/txn counts describe a trailing 24h window, so they only
    # represent "launch activity" if we saw the pool inside its first hour.
    first_hour_traders = None
    first_hour_txns = None
    if first_obs is not None and created is not None:
        observed_at = _as_utc(first_obs[0])
        if observed_at is not None and (observed_at - created).total_seconds() <= 3600:
            first_hour_traders = first_obs[3]
            first_hour_txns = first_obs[4]

    # First hourly candle at/after creation.
    first_hour_volume = None
    for ts, vol, _ in candle_rows:
        ts_utc = _as_utc(ts)
        if created is not None and ts_utc is not None and ts_utc >= created:
            first_hour_volume = vol
            break

    # --- forward-looking features ------------------------------------------
    liquidity_series = [row[1] for row in obs_rows if row[1] is not None]
    peak_liquidity = max(liquidity_series) if liquidity_series else None
    liq_cv = coefficient_of_variation(liquidity_series)

    vol_values = [v for _, v in volumes if v is not None]
    total_volume = sum(vol_values) if vol_values else None

    first_ts = _as_utc(candle_rows[0][0])
    last_ts = _as_utc(candle_rows[-1][0])
    span_hours = (
        (last_ts - first_ts).total_seconds() / 3600.0
        if first_ts and last_ts and last_ts > first_ts
        else None
    )
    persistence = (
        min(1.0, survival.active_hours / span_hours) if span_hours and span_hours > 0 else None
    )

    # --- data quality -------------------------------------------------------
    missing_counts = [row[5] for row in obs_rows if row[5] is not None]
    completeness = None
    if obs_rows:
        # 6 fields are checked per observation by the providers' expected-field list.
        expected_per_obs = 6
        missing_total = sum(missing_counts) if missing_counts else 0
        completeness = max(
            0.0, 1.0 - missing_total / (len(obs_rows) * expected_per_obs)
        )

    return PoolFeature(
        pool_id=pool.id,
        initial_liquidity_usd=initial_liquidity,
        initial_fdv_usd=initial_fdv,
        launch_hour_utc=created.hour if created else None,
        launch_dow=created.weekday() if created else None,
        dex=pool.dex,
        first_hour_volume_usd=first_hour_volume,
        first_hour_unique_traders=first_hour_traders,
        first_hour_txns=first_hour_txns,
        peak_liquidity_usd=peak_liquidity,
        liquidity_cv=liq_cv,
        total_volume_usd=total_volume,
        active_hours=survival.active_hours,
        activity_persistence=persistence,
        realized_volatility=realized_volatility(closes),
        max_drawdown_pct=max_drawdown_pct(closes),
        duration_hours=survival.duration_hours,
        event_observed=survival.event_observed,
        survived_24h=survived_at(survival.duration_hours, survival.event_observed, 24),
        survived_72h=survived_at(survival.duration_hours, survival.event_observed, 72),
        candle_count=len(candle_rows),
        observation_count=len(obs_rows),
        completeness=completeness,
    )


def dataset_counts(session: Session) -> dict[str, int]:
    return {
        "pools": session.scalar(select(func.count()).select_from(Pool)) or 0,
        "observations": session.scalar(select(func.count()).select_from(MarketObservation)) or 0,
        "candles": session.scalar(select(func.count()).select_from(OhlcvCandle)) or 0,
    }
