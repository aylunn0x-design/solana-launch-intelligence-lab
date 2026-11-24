"""Correlation analysis.

Spearman is the default. Pearson measures *linear* association and is highly
sensitive to the extreme outliers that dominate token market data; Spearman
measures monotonic association on ranks and is robust to them. Pearson is
offered, but the result carries a warning when the data are badly skewed.

Multiple comparisons: a correlation matrix over k variables runs k(k-1)/2
tests, so some will look "significant" by chance. `correlation_matrix` reports
Benjamini-Hochberg adjusted p-values alongside the raw ones. Reporting only raw
p-values from a matrix is a textbook data-snooping error.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from app.research.common import (
    CAUSATION_DISCLAIMER,
    MIN_SAMPLE_FOR_INFERENCE,
    StatResult,
)

#: pairwise-complete n below this is not reported
MIN_PAIRWISE_N = 10


def correlation(
    frame: pd.DataFrame, x: str, y: str, method: str = "spearman"
) -> StatResult:
    """Correlation between two variables with CI, n and caveats."""
    for col in (x, y):
        if col not in frame.columns:
            raise KeyError(f"column {col!r} is not in this dataset")

    pair = frame[[x, y]].apply(pd.to_numeric, errors="coerce")
    total = len(pair)
    pair = pair.dropna()
    n = len(pair)
    missing_share = 1.0 - (n / total) if total else 1.0

    warnings = [CAUSATION_DISCLAIMER]
    details = {"x": x, "y": y, "pairwise_complete_n": n}

    if n < MIN_PAIRWISE_N:
        return StatResult(
            method=method,
            statistic_name="not computed",
            statistic=None,
            n=n,
            missing_share=missing_share,
            warnings=warnings
            + [
                f"Only {n} rows have both {x} and {y}. At least {MIN_PAIRWISE_N} are "
                "required before a correlation is reported."
            ],
            details=details,
        )

    xv = pair[x].to_numpy(dtype=float)
    yv = pair[y].to_numpy(dtype=float)

    # A constant variable has no correlation to measure (and scipy warns).
    if np.std(xv) == 0 or np.std(yv) == 0:
        return StatResult(
            method=method,
            statistic_name="not computed",
            statistic=None,
            n=n,
            missing_share=missing_share,
            warnings=warnings
            + [f"One of {x}/{y} is constant in this sample, so correlation is undefined."],
            details=details,
        )

    if method == "pearson":
        coef, p = stats.pearsonr(xv, yv)
        method_label = "Pearson product-moment correlation (linear)"
        for name, vals in ((x, xv), (y, yv)):
            if len(vals) > 2 and abs(stats.skew(vals)) > 2:
                warnings.append(
                    f"{name} is strongly skewed (skewness={stats.skew(vals):.1f}). "
                    "Pearson is sensitive to outliers here; Spearman is more robust."
                )
    else:
        coef, p = stats.spearmanr(xv, yv)
        method_label = "Spearman rank correlation (monotonic, outlier-robust)"

    coef = float(coef)
    lo, hi = fisher_ci(coef, n)

    if n < MIN_SAMPLE_FOR_INFERENCE:
        warnings.append(f"Small sample (n={n}); the interval is wide for a reason.")
    if abs(coef) < 0.1:
        warnings.append("Effect is negligible in size regardless of the p-value.")

    return StatResult(
        method=method_label,
        statistic_name="rho" if method == "spearman" else "r",
        statistic=coef,
        n=n,
        missing_share=missing_share,
        p_value=float(p),
        ci_lower=lo,
        ci_upper=hi,
        effect_size=coef,
        effect_size_name="correlation coefficient (is itself the effect size)",
        warnings=warnings,
        details={
            **details,
            "ci_method": "Fisher z-transformation, 95%",
            "interpretation": _interpret_strength(coef),
        },
    )


def fisher_ci(coef: float, n: int, confidence: float = 0.95) -> tuple[float | None, float | None]:
    """CI for a correlation via the Fisher z-transformation.

    Applied to Spearman's rho as a standard approximation; it is exact for
    Pearson's r under bivariate normality and adequate for rho in practice.
    """
    if n < 4 or abs(coef) >= 1.0:
        return None, None
    z = np.arctanh(coef)
    se = 1.0 / np.sqrt(n - 3)
    crit = stats.norm.ppf(1 - (1 - confidence) / 2)
    return float(np.tanh(z - crit * se)), float(np.tanh(z + crit * se))


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg FDR-adjusted p-values, order preserved."""
    valid = [(i, p) for i, p in enumerate(p_values) if p is not None and not np.isnan(p)]
    adjusted: list[float | None] = [None] * len(p_values)
    if not valid:
        return [float("nan")] * len(p_values)

    m = len(valid)
    valid.sort(key=lambda t: t[1])
    prev = 1.0
    # Walk from largest p downwards, enforcing monotonicity.
    for rank in range(m, 0, -1):
        idx, p = valid[rank - 1]
        value = min(prev, p * m / rank)
        adjusted[idx] = value
        prev = value
    return [a if a is not None else float("nan") for a in adjusted]


def correlation_matrix(
    frame: pd.DataFrame, columns: list[str], method: str = "spearman"
) -> dict:
    """Pairwise correlation matrix with per-pair n and FDR-adjusted p-values."""
    present = [c for c in columns if c in frame.columns]
    missing_cols = [c for c in columns if c not in frame.columns]

    numeric = frame[present].apply(pd.to_numeric, errors="coerce") if present else pd.DataFrame()
    usable = [c for c in present if numeric[c].notna().sum() >= MIN_PAIRWISE_N]
    dropped = [c for c in present if c not in usable]

    k = len(usable)
    matrix = [[None] * k for _ in range(k)]
    n_matrix = [[0] * k for _ in range(k)]
    pairs: list[dict] = []
    raw_p: list[float] = []

    for i in range(k):
        matrix[i][i] = 1.0
        n_matrix[i][i] = int(numeric[usable[i]].notna().sum())
        for j in range(i + 1, k):
            sub = numeric[[usable[i], usable[j]]].dropna()
            n = len(sub)
            n_matrix[i][j] = n_matrix[j][i] = n
            if n < MIN_PAIRWISE_N:
                continue
            a = sub[usable[i]].to_numpy(dtype=float)
            b = sub[usable[j]].to_numpy(dtype=float)
            if np.std(a) == 0 or np.std(b) == 0:
                continue
            if method == "pearson":
                coef, p = stats.pearsonr(a, b)
            else:
                coef, p = stats.spearmanr(a, b)
            matrix[i][j] = matrix[j][i] = float(coef)
            pairs.append(
                {
                    "x": usable[i],
                    "y": usable[j],
                    "coefficient": float(coef),
                    "n": n,
                    "p_value": float(p),
                }
            )
            raw_p.append(float(p))

    adjusted = benjamini_hochberg(raw_p)
    for pair, adj in zip(pairs, adjusted, strict=False):
        pair["p_value_adjusted"] = None if np.isnan(adj) else float(adj)
        pair["significant_after_fdr"] = bool(not np.isnan(adj) and adj < 0.05)
        pair["interpretation"] = _interpret_strength(pair["coefficient"])

    pairs.sort(key=lambda p: abs(p["coefficient"]), reverse=True)

    warnings = [CAUSATION_DISCLAIMER]
    if pairs:
        warnings.append(
            f"{len(pairs)} pairwise tests were run. p-values are adjusted for the "
            "false discovery rate (Benjamini-Hochberg); use the adjusted values, "
            "not the raw ones, when judging significance from this matrix."
        )
    if dropped:
        warnings.append(
            "Excluded for having fewer than "
            f"{MIN_PAIRWISE_N} non-null values: {', '.join(dropped)}."
        )
    if missing_cols:
        warnings.append(f"Not present in this dataset: {', '.join(missing_cols)}.")

    return {
        "method": "Spearman rank" if method != "pearson" else "Pearson linear",
        "columns": usable,
        "matrix": matrix,
        "pairwise_n": n_matrix,
        "pairs": pairs,
        "warnings": warnings,
    }


def _interpret_strength(coef: float) -> str:
    """Plain-language magnitude label. Deliberately conservative."""
    a = abs(coef)
    direction = "positive" if coef > 0 else "negative"
    if a < 0.1:
        return "negligible"
    if a < 0.3:
        return f"weak {direction}"
    if a < 0.5:
        return f"moderate {direction}"
    if a < 0.7:
        return f"strong {direction}"
    return f"very strong {direction}"
