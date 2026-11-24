"""Shared research plumbing: dataset loading, result envelopes, bias warnings.

Two rules shape this module:

1. **No bare numbers.** Every statistic is returned inside `StatResult`, which
   carries the sample size, the missing-data share, the method used, and
   warnings. A correlation coefficient with no n and no caveats is not a
   research result.

2. **Bias is attached to the data, not left to the reader.** `load_dataset`
   inspects the cohort composition of the dataset version and returns warnings
   that every downstream analysis propagates. If a sample is
   survivorship-biased, the result says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DatasetVersion, DiscoveryMethod, Pool, PoolFeature
from app.pipeline.features import FEATURE_BY_NAME

#: Below this many usable rows we refuse to report inferential statistics.
MIN_SAMPLE_FOR_INFERENCE = 20

#: Below this we warn but still report.
SMALL_SAMPLE_WARNING = 50

CAUSATION_DISCLAIMER = (
    "Association only. This is observational data with no randomisation or "
    "controls, so it cannot establish that one variable causes another."
)


@dataclass
class StatResult:
    """A statistic plus everything needed to interpret it honestly."""

    method: str
    statistic_name: str
    statistic: float | None
    n: int
    #: share of the selected rows dropped for missing values, 0.0-1.0
    missing_share: float = 0.0
    p_value: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    effect_size: float | None = None
    effect_size_name: str | None = None
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "statistic_name": self.statistic_name,
            "statistic": _clean(self.statistic),
            "n": self.n,
            "missing_share": round(self.missing_share, 4),
            "p_value": _clean(self.p_value),
            "ci_lower": _clean(self.ci_lower),
            "ci_upper": _clean(self.ci_upper),
            "effect_size": _clean(self.effect_size),
            "effect_size_name": self.effect_size_name,
            "warnings": self.warnings,
            "details": _clean_mapping(self.details),
        }


def _clean(value: Any) -> Any:
    """JSON cannot carry NaN/inf; convert to None and numpy scalars to Python."""
    if value is None:
        return None
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def _clean_mapping(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean_mapping(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_mapping(v) for v in obj]
    return _clean(obj)


@dataclass
class ResearchDataset:
    """A dataset version loaded into a DataFrame, with its caveats."""

    version_id: int
    label: str
    frame: pd.DataFrame
    inactivity_threshold_hours: int
    cohort_warnings: list[str] = field(default_factory=list)
    criteria: dict | None = None

    @property
    def n(self) -> int:
        return len(self.frame)


#: Columns loaded from pool_features, by availability class.
FEATURE_COLUMNS = [
    "initial_liquidity_usd",
    "initial_fdv_usd",
    "launch_hour_utc",
    "launch_dow",
    "dex",
    "first_hour_volume_usd",
    "first_hour_unique_traders",
    "first_hour_txns",
    "peak_liquidity_usd",
    "liquidity_cv",
    "total_volume_usd",
    "active_hours",
    "activity_persistence",
    "realized_volatility",
    "max_drawdown_pct",
    "duration_hours",
    "event_observed",
    "survived_24h",
    "survived_72h",
    "candle_count",
    "observation_count",
    "completeness",
]


def load_dataset(
    session: Session,
    version_id: int | None = None,
    label: str | None = None,
) -> ResearchDataset:
    """Load a dataset version into a DataFrame and compute its bias warnings."""
    if version_id is not None:
        version = session.execute(
            select(DatasetVersion).where(DatasetVersion.id == version_id)
        ).scalars().first()
    elif label is not None:
        version = session.execute(
            select(DatasetVersion).where(DatasetVersion.label == label)
        ).scalars().first()
    else:
        # Default to the most recent version that actually contains pools. A
        # version can legitimately be empty (e.g. an inception cohort whose
        # pools are still too young to have the required candles), and silently
        # defaulting to it would make the whole Research Lab look broken when
        # usable datasets exist alongside it.
        version = session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.pool_count > 0)
            .order_by(DatasetVersion.built_at.desc())
        ).scalars().first()
        if version is None:
            # Nothing non-empty exists: fall back to the newest so the caller
            # sees a real (empty) dataset and its warnings rather than a 404.
            version = session.execute(
                select(DatasetVersion).order_by(DatasetVersion.built_at.desc())
            ).scalars().first()
    if version is None:
        raise LookupError(
            "No dataset version found. Build one with: "
            "python -m app.cli build-dataset --label v1"
        )

    rows = session.execute(
        select(PoolFeature, Pool)
        .join(Pool, PoolFeature.pool_id == Pool.id)
        .where(PoolFeature.dataset_version_id == version.id)
    ).all()

    records: list[dict] = []
    for feature, pool in rows:
        record: dict[str, Any] = {
            "pool_id": pool.id,
            "pool_address": pool.pool_address,
            "token_id": pool.token_id,
            "pool_created_at": pool.pool_created_at,
            "discovery_method": pool.discovery_method.value
            if hasattr(pool.discovery_method, "value")
            else str(pool.discovery_method),
        }
        for col in FEATURE_COLUMNS:
            record[col] = getattr(feature, col, None)
        records.append(record)

    frame = pd.DataFrame.from_records(records)
    if not frame.empty:
        frame["pool_created_at"] = pd.to_datetime(frame["pool_created_at"], utc=True, errors="coerce")

    warnings = _cohort_warnings(frame, version)

    return ResearchDataset(
        version_id=version.id,
        label=version.label,
        frame=frame,
        inactivity_threshold_hours=version.inactivity_threshold_hours,
        cohort_warnings=warnings,
        criteria=version.criteria,
    )


def _cohort_warnings(frame: pd.DataFrame, version: DatasetVersion) -> list[str]:
    """Warnings that must accompany every result from this dataset."""
    warnings: list[str] = []

    if frame.empty:
        warnings.append("Dataset contains no pools. No results can be computed.")
        return warnings

    n = len(frame)
    inception = int((frame["discovery_method"] == DiscoveryMethod.NEW_POOLS_FEED.value).sum())
    biased = n - inception

    if biased > 0:
        share = biased / n
        warnings.append(
            f"SURVIVORSHIP BIAS: {biased}/{n} pools ({share:.0%}) were discovered via "
            "ranked 'top'/'trending' listings, which only include pools that are "
            "currently notable. Pools that launched and died are systematically "
            "under-represented, so survival rates computed here are biased upward "
            "and must not be read as the survival rate of Solana launches."
        )
    if inception == 0:
        warnings.append(
            "No pools in this dataset come from the unbiased new-pools inception "
            "cohort. Inferential claims about launch outcomes are not supportable "
            "from this sample."
        )

    if n < MIN_SAMPLE_FOR_INFERENCE:
        warnings.append(
            f"Very small sample (n={n}). Below n={MIN_SAMPLE_FOR_INFERENCE} this "
            "platform does not treat inferential statistics as meaningful."
        )
    elif n < SMALL_SAMPLE_WARNING:
        warnings.append(
            f"Small sample (n={n}). Estimates are imprecise; prefer the confidence "
            "intervals over point estimates."
        )

    # Censoring load matters for interpreting any survival result.
    if "event_observed" in frame:
        events = frame["event_observed"].dropna()
        if len(events) > 0:
            censored_share = 1.0 - float(events.mean())
            if censored_share > 0.9:
                warnings.append(
                    f"{censored_share:.0%} of pools are right-censored (still active at "
                    "observation time). Survival estimates past the observed range are "
                    "extrapolation, not measurement."
                )

    # Degenerate forward-looking features.
    #
    # peak_liquidity_usd and liquidity_cv are computed across a pool's stored
    # market observations. When a pool has only ONE observation, its "peak"
    # liquidity is just its initial liquidity (so the two correlate at exactly
    # 1.0, which is a tautology, not a finding) and its coefficient of variation
    # is undefined. This is a property of how much polling has happened, not of
    # the market, so it has to be stated rather than left for the reader to
    # notice in a heatmap.
    if "observation_count" in frame:
        obs_counts = pd.to_numeric(frame["observation_count"], errors="coerce").dropna()
        if len(obs_counts) and obs_counts.median() <= 1:
            single = int((obs_counts <= 1).sum())
            warnings.append(
                f"DEGENERATE LIQUIDITY FEATURES: {single}/{len(obs_counts)} pools have only "
                "one market observation, so peak_liquidity_usd equals "
                "initial_liquidity_usd for them (they correlate at 1.0 by construction) "
                "and liquidity_cv cannot be computed. Liquidity-variability results are "
                "not meaningful until the refresh job has polled these pools repeatedly."
            )

    # Observation window: a dataset collected over hours cannot speak to months.
    if "pool_created_at" in frame and frame["pool_created_at"].notna().any():
        span = frame["pool_created_at"].max() - frame["pool_created_at"].min()
        days = span.total_seconds() / 86400.0 if pd.notna(span) else 0
        if days < 7:
            warnings.append(
                f"Launch dates span only {days:.1f} day(s). Results reflect one market "
                "period and cannot be generalised across market regimes (RQ5)."
            )

    warnings.append(
        f"Survival is defined as measurable trading activity with an inactivity "
        f"threshold of {version.inactivity_threshold_hours}h. Conclusions can change "
        "under a different threshold (RQ6)."
    )
    return warnings


def numeric_series(
    frame: pd.DataFrame, column: str
) -> tuple[pd.Series, float]:
    """Return a numeric column with NaNs dropped, plus the share dropped."""
    if column not in frame.columns:
        raise KeyError(f"column {column!r} is not in this dataset")
    raw = pd.to_numeric(frame[column], errors="coerce")
    total = len(raw)
    clean = raw.dropna()
    missing_share = 1.0 - (len(clean) / total) if total else 1.0
    return clean, missing_share


def available_variables(frame: pd.DataFrame) -> list[dict]:
    """Variables usable for analysis, annotated with registry metadata.

    Only reports a variable if it actually has at least two non-null numeric
    values in this dataset, so the UI never offers a variable that cannot be
    plotted.
    """
    out: list[dict] = []
    for col in FEATURE_COLUMNS:
        if col not in frame.columns:
            continue
        spec = FEATURE_BY_NAME.get(col)
        series = pd.to_numeric(frame[col], errors="coerce")
        non_null = int(series.notna().sum())
        if non_null < 2:
            continue
        out.append(
            {
                "name": col,
                "non_null_count": non_null,
                "missing_share": round(1.0 - non_null / len(frame), 4) if len(frame) else 1.0,
                "availability": spec.availability.value if spec else "unknown",
                "description": spec.description if spec else "",
                "unit": spec.unit if spec else None,
                "is_legal_model_input": bool(spec and spec.availability.value == "at_launch"),
            }
        )
    return out


Alternative = Literal["two-sided", "less", "greater"]
