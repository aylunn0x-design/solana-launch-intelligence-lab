"""Descriptive statistics, distributions, and group comparisons.

Design choices worth defending in an interview:

* **Medians and IQR lead, not means.** New-token liquidity and volume are
  extremely right-skewed (a handful of pools carry most of the volume), so the
  mean is a poor summary. Both are reported, with skewness, so the reader can
  see why.
* **Histograms are computed server-side** on the full column rather than sending
  every row to the browser, and the bin edges are returned so the chart cannot
  silently rebin and mislead.
* **Group comparisons use Mann-Whitney U by default.** It does not assume
  normality, which these distributions badly violate. A rank-biserial
  correlation is reported as the effect size, because a p-value alone says
  nothing about magnitude.
* **Bootstrap confidence intervals** are used for the median difference, since
  no clean closed form exists for it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from app.research.common import (
    CAUSATION_DISCLAIMER,
    MIN_SAMPLE_FOR_INFERENCE,
    StatResult,
    numeric_series,
)

#: Fixed seed so bootstrap intervals are reproducible across runs.
BOOTSTRAP_SEED = 20261124
BOOTSTRAP_ITERATIONS = 2000


def describe_variable(frame: pd.DataFrame, column: str) -> dict:
    """Full descriptive summary of one variable, including missingness."""
    series, missing_share = numeric_series(frame, column)
    n = len(series)

    if n == 0:
        return {
            "column": column,
            "n": 0,
            "missing_share": round(missing_share, 4),
            "available": False,
            "message": f"No non-null numeric values for {column!r} in this dataset.",
        }

    values = series.to_numpy(dtype=float)
    quantiles = np.percentile(values, [1, 5, 25, 50, 75, 95, 99]) if n >= 2 else [None] * 7

    summary = {
        "column": column,
        "n": n,
        "missing_share": round(missing_share, 4),
        "available": True,
        "mean": float(np.mean(values)),
        "std": float(np.std(values, ddof=1)) if n > 1 else None,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p01": _f(quantiles[0]),
        "p05": _f(quantiles[1]),
        "q1": _f(quantiles[2]),
        "median": _f(quantiles[3]),
        "q3": _f(quantiles[4]),
        "p95": _f(quantiles[5]),
        "p99": _f(quantiles[6]),
        "zero_count": int(np.sum(values == 0)),
        "negative_count": int(np.sum(values < 0)),
    }
    summary["iqr"] = (
        summary["q3"] - summary["q1"]
        if summary["q3"] is not None and summary["q1"] is not None
        else None
    )
    if n > 2:
        summary["skewness"] = float(stats.skew(values))
        summary["kurtosis"] = float(stats.kurtosis(values))
    else:
        summary["skewness"] = None
        summary["kurtosis"] = None

    notes: list[str] = []
    if summary["skewness"] is not None and abs(summary["skewness"]) > 2:
        notes.append(
            f"Strongly skewed (skewness={summary['skewness']:.1f}). The median "
            f"({summary['median']:.6g}) describes a typical pool far better than the "
            f"mean ({summary['mean']:.6g})."
        )
    if missing_share > 0.3:
        notes.append(
            f"{missing_share:.0%} of rows are missing this variable. Any analysis "
            "using it is effectively a complete-case analysis on a subsample."
        )
    summary["notes"] = notes
    return summary


def histogram(
    frame: pd.DataFrame, column: str, bins: int = 30, log_scale: bool = False
) -> dict:
    """Server-side histogram with explicit bin edges.

    `log_scale` bins log10(x) for strictly positive values. Useful for liquidity
    and volume, which span many orders of magnitude; zero/negative values are
    excluded and counted separately rather than silently dropped.
    """
    series, missing_share = numeric_series(frame, column)
    if len(series) == 0:
        return {
            "column": column,
            "n": 0,
            "available": False,
            "missing_share": round(missing_share, 4),
            "message": f"No data to plot for {column!r}.",
        }

    values = series.to_numpy(dtype=float)
    excluded_nonpositive = 0
    scale = "linear"

    if log_scale:
        positive = values[values > 0]
        excluded_nonpositive = int(len(values) - len(positive))
        if len(positive) < 2:
            return {
                "column": column,
                "n": int(len(values)),
                "available": False,
                "missing_share": round(missing_share, 4),
                "message": (
                    f"Cannot plot {column!r} on a log scale: fewer than two "
                    "strictly positive values."
                ),
            }
        values = np.log10(positive)
        scale = "log10"

    counts, edges = np.histogram(values, bins=min(bins, max(5, len(values))))
    return {
        "column": column,
        "n": int(counts.sum()),
        "available": True,
        "missing_share": round(missing_share, 4),
        "scale": scale,
        "bin_edges": [float(e) for e in edges],
        "counts": [int(c) for c in counts],
        "excluded_nonpositive": excluded_nonpositive,
        "note": (
            f"{excluded_nonpositive} non-positive value(s) excluded for the log scale."
            if excluded_nonpositive
            else ""
        ),
    }


def rank_biserial_from_u(u_statistic: float, n1: int, n2: int) -> float:
    """Rank-biserial correlation: a bounded effect size for Mann-Whitney U.

    r = 2U/(n1*n2) - 1, in [-1, 1]. 0 means the groups overlap completely.
    """
    if n1 == 0 or n2 == 0:
        return float("nan")
    return 2.0 * u_statistic / (n1 * n2) - 1.0


def compare_groups(
    frame: pd.DataFrame,
    value_column: str,
    group_column: str,
    *,
    group_a: object | None = None,
    group_b: object | None = None,
    method: str = "mannwhitney",
) -> StatResult:
    """Compare a numeric variable between two groups.

    Defaults to Mann-Whitney U (no normality assumption). Welch's t-test is
    available but flagged, since these distributions are heavily skewed.
    """
    if value_column not in frame.columns:
        raise KeyError(f"{value_column!r} is not in this dataset")
    if group_column not in frame.columns:
        raise KeyError(f"{group_column!r} is not in this dataset")

    work = frame[[value_column, group_column]].copy()
    work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
    total_rows = len(work)
    work = work.dropna()

    # Booleans and small-cardinality values act as groups directly.
    groups = list(pd.unique(work[group_column]))
    if group_a is None or group_b is None:
        if len(groups) != 2:
            raise ValueError(
                f"{group_column!r} has {len(groups)} distinct values; specify "
                "group_a and group_b explicitly to compare two of them."
            )
        group_a, group_b = groups[0], groups[1]

    a = work.loc[work[group_column] == group_a, value_column].to_numpy(dtype=float)
    b = work.loc[work[group_column] == group_b, value_column].to_numpy(dtype=float)
    n1, n2 = len(a), len(b)
    n = n1 + n2
    missing_share = 1.0 - (n / total_rows) if total_rows else 1.0

    warnings = [CAUSATION_DISCLAIMER]
    details = {
        "group_column": group_column,
        "group_a": str(group_a),
        "group_b": str(group_b),
        "n_a": n1,
        "n_b": n2,
        "median_a": float(np.median(a)) if n1 else None,
        "median_b": float(np.median(b)) if n2 else None,
        "mean_a": float(np.mean(a)) if n1 else None,
        "mean_b": float(np.mean(b)) if n2 else None,
    }

    if n1 < 3 or n2 < 3:
        return StatResult(
            method=method,
            statistic_name="not computed",
            statistic=None,
            n=n,
            missing_share=missing_share,
            warnings=warnings
            + [
                f"Refusing to test: group sizes are {n1} and {n2}. At least 3 "
                "observations per group are required."
            ],
            details=details,
        )

    if n < MIN_SAMPLE_FOR_INFERENCE:
        warnings.append(
            f"Total sample is {n}, below the platform's inferential threshold of "
            f"{MIN_SAMPLE_FOR_INFERENCE}. Treat this as exploratory."
        )

    if method == "welch":
        stat, p = stats.ttest_ind(a, b, equal_var=False)
        pooled_sd = np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2)
        effect = float((np.mean(a) - np.mean(b)) / pooled_sd) if pooled_sd > 0 else None
        warnings.append(
            "Welch's t-test compares means and assumes approximate normality. "
            "These distributions are typically heavily skewed, so Mann-Whitney U "
            "is usually the more defensible choice."
        )
        result = StatResult(
            method="Welch's t-test (unequal variances)",
            statistic_name="t",
            statistic=float(stat),
            n=n,
            missing_share=missing_share,
            p_value=float(p),
            effect_size=effect,
            effect_size_name="Cohen's d",
            warnings=warnings,
            details=details,
        )
    else:
        stat, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        effect = rank_biserial_from_u(float(stat), n1, n2)
        result = StatResult(
            method="Mann-Whitney U (two-sided, no normality assumption)",
            statistic_name="U",
            statistic=float(stat),
            n=n,
            missing_share=missing_share,
            p_value=float(p),
            effect_size=float(effect),
            effect_size_name="rank-biserial correlation",
            warnings=warnings,
            details=details,
        )

    # Bootstrap CI for the difference in medians: no clean closed form exists.
    lo, hi = bootstrap_median_difference_ci(a, b)
    result.ci_lower, result.ci_upper = lo, hi
    result.details["median_difference"] = (
        float(np.median(a) - np.median(b)) if n1 and n2 else None
    )
    result.details["ci_interpretation"] = (
        "95% bootstrap CI for (median of group A - median of group B), "
        f"{BOOTSTRAP_ITERATIONS} resamples, seed {BOOTSTRAP_SEED}."
    )
    return result


def bootstrap_median_difference_ci(
    a: np.ndarray, b: np.ndarray, *, iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = 0.95, seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """Percentile bootstrap CI for the difference in medians. Reproducible."""
    if len(a) < 3 or len(b) < 3:
        return None, None
    rng = np.random.default_rng(seed)
    diffs = np.empty(iterations, dtype=float)
    for i in range(iterations):
        diffs[i] = np.median(rng.choice(a, size=len(a), replace=True)) - np.median(
            rng.choice(b, size=len(b), replace=True)
        )
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.percentile(diffs, [100 * alpha, 100 * (1 - alpha)])
    return float(lo), float(hi)


def bootstrap_mean_ci(
    values: np.ndarray, *, iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = 0.95, seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    if len(values) < 3:
        return None, None
    rng = np.random.default_rng(seed)
    means = np.array(
        [np.mean(rng.choice(values, size=len(values), replace=True)) for _ in range(iterations)]
    )
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.percentile(means, [100 * alpha, 100 * (1 - alpha)])
    return float(lo), float(hi)


def cohort_breakdown(frame: pd.DataFrame, column: str = "dex", top_n: int = 12) -> dict:
    """Counts and median outcomes by a categorical column."""
    if column not in frame.columns or frame.empty:
        return {"column": column, "available": False, "groups": []}

    work = frame.copy()
    work[column] = work[column].fillna("(unknown)")
    grouped = work.groupby(column, dropna=False)

    rows = []
    for key, sub in grouped:
        duration = pd.to_numeric(sub.get("duration_hours"), errors="coerce").dropna()
        liquidity = pd.to_numeric(sub.get("initial_liquidity_usd"), errors="coerce").dropna()
        rows.append(
            {
                "group": str(key),
                "n": int(len(sub)),
                "median_duration_hours": float(duration.median()) if len(duration) else None,
                "median_initial_liquidity_usd": float(liquidity.median())
                if len(liquidity)
                else None,
                "event_rate": float(
                    pd.to_numeric(sub.get("event_observed"), errors="coerce").dropna().mean()
                )
                if "event_observed" in sub and sub["event_observed"].notna().any()
                else None,
            }
        )
    rows.sort(key=lambda r: r["n"], reverse=True)
    return {"column": column, "available": True, "groups": rows[:top_n]}


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(out) or np.isinf(out) else out
