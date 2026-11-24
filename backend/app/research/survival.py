"""Survival analysis: Kaplan-Meier and Cox proportional hazards.

Why survival analysis rather than a simple "what fraction died" rate
--------------------------------------------------------------------
Most pools in any sample are still alive when we look. Computing a death rate
over them would either throw those pools away (biasing the result) or count
them as survivors forever (also biasing it). Survival analysis handles this
directly: a pool still active at observation time is **right-censored** — it
contributes information up to the point we stopped watching, and nothing after.

Event definition (from `pipeline.features.compute_survival`)
    event  = measurable trading activity stopped and stayed stopped for longer
             than the dataset's inactivity threshold
    time   = hours from pool_created_at to last activity (event) or to now
             (censored)

Assumptions, and how they are checked
-------------------------------------
Kaplan-Meier assumes censoring is *independent* of the event process. Here that
assumption is imperfect and the code says so: pools are censored because the
observation window ended, which is unrelated to the pool, but the retrospective
sample's *entry* into the dataset is not independent of survival, which is the
survivorship-bias warning carried from `common.load_dataset`.

Cox proportional hazards assumes hazard ratios are constant over time. This is
tested with `lifelines`' `proportional_hazard_test` and the result is reported,
not hidden. A violated assumption is reported as a warning rather than quietly
ignored.
"""

from __future__ import annotations

import logging
import warnings as _pywarnings

import numpy as np
import pandas as pd

from app.research.common import (
    CAUSATION_DISCLAIMER,
    MIN_SAMPLE_FOR_INFERENCE,
    ResearchDataset,
)

logger = logging.getLogger(__name__)

#: Minimum events (not rows) before a Cox model is attempted. The rule of thumb
#: is ~10 events per covariate; below 10 events total, nothing is estimable.
MIN_EVENTS_FOR_COX = 10
#: events-per-variable below which the model is reported but flagged as unstable
EPV_WARNING_THRESHOLD = 10


def _survival_frame(dataset: ResearchDataset) -> tuple[pd.DataFrame, float]:
    """Rows usable for survival analysis, plus the share dropped."""
    frame = dataset.frame
    if frame.empty:
        return frame, 1.0
    work = frame.copy()
    work["duration_hours"] = pd.to_numeric(work["duration_hours"], errors="coerce")
    work["event_observed"] = work["event_observed"].map(
        lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else bool(v)
    )
    total = len(work)
    work = work.dropna(subset=["duration_hours", "event_observed"])
    # A non-positive duration cannot enter a lifetime model.
    work = work[work["duration_hours"] > 0]
    dropped_share = 1.0 - (len(work) / total) if total else 1.0
    return work, dropped_share


def kaplan_meier(
    dataset: ResearchDataset, group_column: str | None = None, max_groups: int = 6
) -> dict:
    """Kaplan-Meier survival curve, optionally stratified by a column.

    Returns the step function (time, survival, CI) ready to plot, plus median
    survival and, when stratified, a log-rank test between groups.
    """
    from lifelines import KaplanMeierFitter
    from lifelines.statistics import multivariate_logrank_test

    work, dropped_share = _survival_frame(dataset)
    base_warnings = list(dataset.cohort_warnings) + [CAUSATION_DISCLAIMER]

    if len(work) < MIN_SAMPLE_FOR_INFERENCE:
        return {
            "available": False,
            "n": len(work),
            "message": (
                f"Only {len(work)} pool(s) have a usable duration and event status "
                f"(need at least {MIN_SAMPLE_FOR_INFERENCE}). Ingest more data or "
                "lower the dataset's min_candles requirement."
            ),
            "warnings": base_warnings,
        }

    n = len(work)
    events = int(work["event_observed"].sum())
    censored = n - events

    result: dict = {
        "available": True,
        "n": n,
        "events_observed": events,
        "censored": censored,
        "censored_share": round(censored / n, 4),
        "missing_share": round(dropped_share, 4),
        "method": "Kaplan-Meier product-limit estimator",
        "event_definition": (
            "Trading activity stopped for longer than "
            f"{dataset.inactivity_threshold_hours}h (the dataset's inactivity threshold)."
        ),
        "time_unit": "hours since pool creation",
        "warnings": base_warnings,
        "curves": [],
    }

    if events == 0:
        result["available"] = False
        result["message"] = (
            "No events were observed: every pool in this dataset was still active at "
            "observation time. A survival curve cannot be estimated from fully "
            "censored data."
        )
        return result

    def fit_one(sub: pd.DataFrame, label: str) -> dict:
        kmf = KaplanMeierFitter()
        kmf.fit(
            sub["duration_hours"].to_numpy(dtype=float),
            event_observed=sub["event_observed"].to_numpy(dtype=bool),
            label=label,
        )
        sf = kmf.survival_function_
        ci = kmf.confidence_interval_
        times = [float(t) for t in sf.index]
        median = kmf.median_survival_time_
        return {
            "label": label,
            "n": int(len(sub)),
            "events": int(sub["event_observed"].sum()),
            "times": times,
            "survival": [float(v) for v in sf.iloc[:, 0]],
            "ci_lower": [float(v) for v in ci.iloc[:, 0]],
            "ci_upper": [float(v) for v in ci.iloc[:, 1]],
            "median_survival_hours": (
                None if median is None or np.isinf(median) or pd.isna(median) else float(median)
            ),
            "median_note": (
                "Median not reached within the observed window: more than half of "
                "the pools were still active when observation stopped."
                if median is None or pd.isna(median) or np.isinf(median)
                else ""
            ),
        }

    if group_column and group_column in work.columns:
        work = work.copy()
        work[group_column] = work[group_column].fillna("(unknown)").astype(str)
        counts = work[group_column].value_counts()
        keep = list(counts[counts >= 5].head(max_groups).index)
        dropped_groups = [g for g in counts.index if g not in keep]

        if len(keep) < 2:
            result["warnings"].append(
                f"Could not stratify by {group_column!r}: fewer than two groups have "
                "at least 5 pools. Showing the pooled curve instead."
            )
            result["curves"].append(fit_one(work, "all pools"))
        else:
            subset = work[work[group_column].isin(keep)]
            for label in keep:
                result["curves"].append(
                    fit_one(subset[subset[group_column] == label], str(label))
                )
            result["group_column"] = group_column
            if dropped_groups:
                result["warnings"].append(
                    f"Groups with fewer than 5 pools were excluded from the "
                    f"stratification: {', '.join(map(str, dropped_groups[:8]))}."
                )
            # Log-rank test: do the curves differ?
            try:
                test = multivariate_logrank_test(
                    subset["duration_hours"].to_numpy(dtype=float),
                    subset[group_column].to_numpy(),
                    subset["event_observed"].to_numpy(dtype=bool),
                )
                result["logrank"] = {
                    "test": "multivariate log-rank",
                    "statistic": float(test.test_statistic),
                    "p_value": float(test.p_value),
                    "interpretation": (
                        "The survival curves differ more than chance would explain."
                        if test.p_value < 0.05
                        else "No statistically detectable difference between the curves."
                    ),
                }
            except Exception as exc:  # noqa: BLE001 - diagnostics, not control flow
                logger.warning("log-rank test failed: %s", exc)
                result["logrank"] = {"error": f"log-rank test could not be computed: {exc}"}
    else:
        result["curves"].append(fit_one(work, "all pools"))

    if censored / n > 0.5:
        result["warnings"].append(
            f"{censored / n:.0%} of pools are right-censored. The curve is reliable "
            "only over the range where pools remain at risk; its tail is weakly "
            "identified."
        )
    return result


def cox_regression(
    dataset: ResearchDataset,
    covariates: list[str] | None = None,
    *,
    log_transform: bool = True,
) -> dict:
    """Cox proportional hazards model on launch-time covariates.

    Only features known at launch are permitted as covariates — enforced via
    `assert_no_leakage`. Using a forward-looking feature here would predict a
    pool's lifetime from data generated during that lifetime.
    """
    from lifelines import CoxPHFitter
    from lifelines.statistics import proportional_hazard_test

    from app.pipeline.features import assert_no_leakage, model_input_features

    requested = covariates or ["initial_liquidity_usd", "first_hour_volume_usd"]
    # Fail loudly rather than silently dropping an illegal covariate.
    assert_no_leakage(requested)

    work, dropped_share = _survival_frame(dataset)
    base_warnings = list(dataset.cohort_warnings) + [CAUSATION_DISCLAIMER]

    numeric_covs: list[str] = []
    for col in requested:
        if col not in work.columns:
            base_warnings.append(f"Covariate {col!r} is not present in this dataset; skipped.")
            continue
        series = pd.to_numeric(work[col], errors="coerce")
        if series.notna().sum() < MIN_EVENTS_FOR_COX:
            base_warnings.append(
                f"Covariate {col!r} has only {int(series.notna().sum())} non-null "
                "values; skipped."
            )
            continue
        work[col] = series
        numeric_covs.append(col)

    if not numeric_covs:
        return {
            "available": False,
            "message": (
                "No usable launch-time covariates in this dataset. "
                f"Legal model inputs are: {', '.join(model_input_features())}."
            ),
            "warnings": base_warnings,
        }

    model_frame = work[["duration_hours", "event_observed", *numeric_covs]].dropna()
    n = len(model_frame)
    events = int(model_frame["event_observed"].sum())

    if events < MIN_EVENTS_FOR_COX:
        return {
            "available": False,
            "n": n,
            "events_observed": events,
            "message": (
                f"Only {events} event(s) observed; a Cox model needs at least "
                f"{MIN_EVENTS_FOR_COX} and ideally {EPV_WARNING_THRESHOLD} per covariate. "
                "Most pools here are still active (right-censored)."
            ),
            "warnings": base_warnings,
        }

    transformed: list[str] = []
    if log_transform:
        # Liquidity and volume span orders of magnitude; log1p keeps the
        # hazard-ratio interpretation sane and stops a single whale pool from
        # dominating the fit. Applied only to non-negative columns.
        for col in numeric_covs:
            if (model_frame[col] >= 0).all():
                model_frame[f"log1p_{col}"] = np.log1p(model_frame[col])
                transformed.append(col)
        if transformed:
            for col in transformed:
                model_frame = model_frame.drop(columns=[col])
            numeric_covs = [
                f"log1p_{c}" if c in transformed else c for c in numeric_covs
            ]

    epv = events / max(1, len(numeric_covs))
    if epv < EPV_WARNING_THRESHOLD:
        base_warnings.append(
            f"Only {epv:.1f} events per covariate ({events} events, "
            f"{len(numeric_covs)} covariates). Below ~{EPV_WARNING_THRESHOLD} the "
            "coefficients are unstable; treat them as indicative, not estimates."
        )

    fitter = CoxPHFitter()
    try:
        with _pywarnings.catch_warnings():
            _pywarnings.simplefilter("ignore")
            fitter.fit(
                model_frame,
                duration_col="duration_hours",
                event_col="event_observed",
            )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller
        return {
            "available": False,
            "n": n,
            "events_observed": events,
            "message": f"Cox model failed to converge: {exc}",
            "warnings": base_warnings
            + [
                "Non-convergence usually means too few events, a covariate with no "
                "variation, or near-perfect separation."
            ],
        }

    summary = fitter.summary
    coefficients = []
    for name, row in summary.iterrows():
        coefficients.append(
            {
                "covariate": str(name),
                "coefficient": float(row["coef"]),
                "hazard_ratio": float(row["exp(coef)"]),
                "se": float(row["se(coef)"]),
                "ci_lower_hr": float(row["exp(coef) lower 95%"]),
                "ci_upper_hr": float(row["exp(coef) upper 95%"]),
                "p_value": float(row["p"]),
                "interpretation": _interpret_hazard_ratio(
                    str(name), float(row["exp(coef)"]), float(row["p"])
                ),
            }
        )

    # Proportional-hazards assumption check: report it, do not bury it.
    ph_report: dict = {}
    try:
        with _pywarnings.catch_warnings():
            _pywarnings.simplefilter("ignore")
            ph = proportional_hazard_test(fitter, model_frame, time_transform="rank")
        violations = []
        for name, row in ph.summary.iterrows():
            p = float(row["p"])
            if p < 0.05:
                violations.append(f"{name} (p={p:.4f})")
        ph_report = {
            "test": "proportional_hazard_test (rank time transform)",
            "violations": violations,
            "passed": not violations,
            "note": (
                "Proportional-hazards assumption not rejected for any covariate."
                if not violations
                else "ASSUMPTION VIOLATED for: "
                + ", ".join(violations)
                + ". The hazard ratio for those covariates is not constant over time, "
                "so a single ratio misrepresents the relationship.",
            ),
        }
        if violations:
            base_warnings.append(
                "Cox proportional-hazards assumption is violated for: "
                + ", ".join(violations)
                + ". Interpret those hazard ratios with caution."
            )
    except Exception as exc:  # noqa: BLE001
        ph_report = {"error": f"assumption test could not be run: {exc}"}

    return {
        "available": True,
        "method": "Cox proportional hazards",
        "n": n,
        "events_observed": events,
        "censored": n - events,
        "missing_share": round(dropped_share, 4),
        "events_per_covariate": round(epv, 2),
        "concordance_index": float(fitter.concordance_index_),
        "concordance_note": (
            "Concordance (c-index) is the share of comparable pool pairs ranked in "
            "the correct order. 0.5 is coin-flip; this is a goodness-of-fit measure, "
            "not out-of-sample accuracy."
        ),
        "log_likelihood": float(fitter.log_likelihood_),
        "covariates": numeric_covs,
        "log_transformed": transformed,
        "coefficients": coefficients,
        "proportional_hazards_test": ph_report,
        "warnings": base_warnings,
    }


def _interpret_hazard_ratio(name: str, hr: float, p: float) -> str:
    if p >= 0.05:
        return (
            f"No statistically detectable association between {name} and the hazard "
            f"of going inactive (p={p:.3f})."
        )
    if hr < 1:
        return (
            f"A higher {name} is associated with a {(1 - hr) * 100:.0f}% lower hazard "
            "of going inactive per unit increase — i.e. longer measured activity."
        )
    return (
        f"A higher {name} is associated with a {(hr - 1) * 100:.0f}% higher hazard of "
        "going inactive per unit increase."
    )


def survival_threshold_sensitivity(
    dataset_frames: dict[int, ResearchDataset]
) -> dict:
    """Compare survival summaries across inactivity thresholds (RQ6).

    Takes {threshold_hours: dataset} and reports how the event rate and median
    survival move with the definition. If conclusions flip across thresholds,
    they were an artefact of the definition, not a finding.
    """
    rows = []
    for threshold, dataset in sorted(dataset_frames.items()):
        work, _ = _survival_frame(dataset)
        if work.empty:
            rows.append({"threshold_hours": threshold, "n": 0, "available": False})
            continue
        events = int(work["event_observed"].sum())
        rows.append(
            {
                "threshold_hours": threshold,
                "n": int(len(work)),
                "events_observed": events,
                "event_rate": round(events / len(work), 4),
                "median_duration_observed": float(work["duration_hours"].median()),
                "available": True,
            }
        )
    return {
        "research_question": "RQ6: how sensitive are results to the survival definition?",
        "rows": rows,
        "warnings": [
            "A conclusion that holds at one inactivity threshold but not another is a "
            "property of the definition, not of the market."
        ],
    }
