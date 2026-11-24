"""Research Lab endpoints.

Every response carries the sample size, the missing-data share, the method used,
and the warnings attached to the dataset. Results are never returned as a bare
number.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.pipeline.features import (
    FEATURE_REGISTRY,
    LeakageError,
    model_input_features,
    outcome_features,
)
from app.research import correlation as corr_mod
from app.research import descriptive, modeling, survival
from app.research.common import CAUSATION_DISCLAIMER, available_variables, load_dataset

router = APIRouter(prefix="/research", tags=["research"])

DEFAULT_MATRIX_COLUMNS = [
    "initial_liquidity_usd",
    "initial_fdv_usd",
    "first_hour_volume_usd",
    "peak_liquidity_usd",
    "liquidity_cv",
    "total_volume_usd",
    "active_hours",
    "activity_persistence",
    "realized_volatility",
    "max_drawdown_pct",
    "duration_hours",
]


@router.get("/summary")
def research_summary(
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    label: str | None = None,
) -> dict:
    """What can be studied with the current dataset, and what cannot."""
    dataset = load_dataset(session, dataset_version_id, label)
    frame = dataset.frame

    return {
        "dataset": {
            "version_id": dataset.version_id,
            "label": dataset.label,
            "n_pools": dataset.n,
            "inactivity_threshold_hours": dataset.inactivity_threshold_hours,
            "criteria": dataset.criteria,
        },
        "cohort_warnings": dataset.cohort_warnings,
        "variables": available_variables(frame),
        "feature_availability": {
            "legal_model_inputs": model_input_features(),
            "outcomes": outcome_features(),
            "explanation": (
                "Only features known at launch may be used as model inputs. "
                "Forward-looking features describe what happened after launch and "
                "would leak outcome information into a predictor."
            ),
        },
        "cohort_breakdown_by_dex": descriptive.cohort_breakdown(frame, "dex"),
        "cohort_breakdown_by_discovery": descriptive.cohort_breakdown(
            frame, "discovery_method"
        ),
        "disclaimer": CAUSATION_DISCLAIMER,
    }


@router.get("/variables")
def variables(
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
) -> dict:
    """Feature registry joined with per-variable availability in this dataset."""
    dataset = load_dataset(session, dataset_version_id)
    present = {v["name"]: v for v in available_variables(dataset.frame)}
    return {
        "dataset_version_id": dataset.version_id,
        "variables": [
            {
                "name": spec.name,
                "dtype": spec.dtype,
                "unit": spec.unit,
                "window": spec.window,
                "availability": spec.availability.value,
                "description": spec.description,
                "is_legal_model_input": spec.availability.value == "at_launch",
                "present_in_dataset": spec.name in present,
                "non_null_count": present.get(spec.name, {}).get("non_null_count", 0),
                "missing_share": present.get(spec.name, {}).get("missing_share", 1.0),
            }
            for spec in FEATURE_REGISTRY
        ],
    }


@router.get("/describe")
def describe(
    column: str,
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
) -> dict:
    """Descriptive statistics for one variable."""
    dataset = load_dataset(session, dataset_version_id)
    summary = descriptive.describe_variable(dataset.frame, column)
    summary["dataset_version_id"] = dataset.version_id
    summary["cohort_warnings"] = dataset.cohort_warnings
    return summary


@router.get("/histogram")
def histogram(
    column: str,
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    bins: int = Query(30, ge=5, le=100),
    log_scale: bool = False,
) -> dict:
    """Distribution of one variable, binned server-side."""
    dataset = load_dataset(session, dataset_version_id)
    result = descriptive.histogram(dataset.frame, column, bins=bins, log_scale=log_scale)
    result["dataset_version_id"] = dataset.version_id
    result["cohort_warnings"] = dataset.cohort_warnings
    return result


@router.get("/correlation")
def correlation(
    x: str,
    y: str,
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    method: Literal["spearman", "pearson"] = "spearman",
) -> dict:
    """Correlation between two variables, with CI and caveats."""
    dataset = load_dataset(session, dataset_version_id)
    result = corr_mod.correlation(dataset.frame, x, y, method=method)
    payload = result.as_dict()
    payload["warnings"] = payload["warnings"] + dataset.cohort_warnings
    payload["dataset_version_id"] = dataset.version_id
    return payload


@router.get("/correlation/matrix")
def correlation_matrix(
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    method: Literal["spearman", "pearson"] = "spearman",
    columns: list[str] | None = Query(None),
) -> dict:
    """Correlation matrix with FDR-adjusted p-values."""
    dataset = load_dataset(session, dataset_version_id)
    result = corr_mod.correlation_matrix(
        dataset.frame, columns or DEFAULT_MATRIX_COLUMNS, method=method
    )
    result["warnings"] = result["warnings"] + dataset.cohort_warnings
    result["dataset_version_id"] = dataset.version_id
    result["n_pools"] = dataset.n
    return result


@router.get("/compare-groups")
def compare_groups(
    value_column: str,
    group_column: str,
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    group_a: str | None = None,
    group_b: str | None = None,
    method: Literal["mannwhitney", "welch"] = "mannwhitney",
) -> dict:
    """Compare a numeric variable between two groups (cohort comparison)."""
    dataset = load_dataset(session, dataset_version_id)
    result = descriptive.compare_groups(
        dataset.frame,
        value_column,
        group_column,
        group_a=group_a,
        group_b=group_b,
        method=method,
    )
    payload = result.as_dict()
    payload["warnings"] = payload["warnings"] + dataset.cohort_warnings
    payload["dataset_version_id"] = dataset.version_id
    return payload


@router.get("/survival")
def survival_curve(
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    group_column: str | None = Query(
        None, description="Optional column to stratify the curve by, e.g. dex"
    ),
) -> dict:
    """Kaplan-Meier survival curve, optionally stratified, with a log-rank test."""
    dataset = load_dataset(session, dataset_version_id)
    result = survival.kaplan_meier(dataset, group_column)
    result["dataset_version_id"] = dataset.version_id
    result["dataset_label"] = dataset.label
    return result


@router.get("/survival/cox")
def cox(
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    covariates: list[str] | None = Query(None),
) -> dict:
    """Cox proportional hazards model on launch-time covariates."""
    dataset = load_dataset(session, dataset_version_id)
    try:
        result = survival.cox_regression(dataset, covariates)
    except LeakageError as exc:
        return {
            "available": False,
            "error": "leakage_blocked",
            "message": str(exc),
            "legal_model_inputs": model_input_features(),
        }
    result["dataset_version_id"] = dataset.version_id
    return result


@router.get("/survival/sensitivity")
def survival_sensitivity(
    session: Session = Depends(get_session),
    labels: list[str] | None = Query(
        None, description="Dataset labels built with different inactivity thresholds"
    ),
) -> dict:
    """RQ6: how much do survival results move with the inactivity threshold?"""
    from sqlalchemy import select

    from app.models import DatasetVersion

    if labels:
        versions = [
            v
            for v in session.execute(
                select(DatasetVersion).where(DatasetVersion.label.in_(labels))
            ).scalars()
        ]
    else:
        versions = list(
            session.execute(
                select(DatasetVersion).order_by(DatasetVersion.built_at.desc()).limit(6)
            ).scalars()
        )

    if not versions:
        return {
            "available": False,
            "message": (
                "No dataset versions found. Build versions at different thresholds, e.g. "
                "`build-dataset --label t12 --inactivity-hours 12` and "
                "`build-dataset --label t48 --inactivity-hours 48`."
            ),
        }

    datasets = {}
    for version in versions:
        loaded = load_dataset(session, version.id)
        datasets[version.inactivity_threshold_hours] = loaded

    result = survival.survival_threshold_sensitivity(datasets)
    result["available"] = True
    result["versions_compared"] = [
        {
            "id": v.id,
            "label": v.label,
            "inactivity_threshold_hours": v.inactivity_threshold_hours,
            "pool_count": v.pool_count,
        }
        for v in versions
    ]
    if len({v.inactivity_threshold_hours for v in versions}) < 2:
        result["warnings"].append(
            "All compared versions use the same inactivity threshold, so this is not "
            "yet a sensitivity analysis. Build versions at different thresholds."
        )
    return result


class ModelRequest(BaseModel):
    """Request body for fitting classifiers."""

    dataset_version_id: int | None = None
    target: Literal["survived_24h", "survived_72h", "event_observed"] = "survived_24h"
    features: list[str] | None = Field(
        default=None, description="Must all be launch-time features"
    )
    test_fraction: float = Field(default=0.3, gt=0.05, lt=0.6)


@router.post("/model")
def fit_models(
    body: ModelRequest, session: Session = Depends(get_session)
) -> dict:
    """Fit a baseline, a logistic regression and a random forest, and compare them."""
    dataset = load_dataset(session, body.dataset_version_id)
    try:
        result = modeling.classify_survival(
            dataset,
            target=body.target,
            features=body.features,
            test_fraction=body.test_fraction,
        )
    except LeakageError as exc:
        return {
            "available": False,
            "error": "leakage_blocked",
            "message": str(exc),
            "legal_model_inputs": model_input_features(),
        }
    result["dataset_version_id"] = dataset.version_id
    result["dataset_label"] = dataset.label
    return result
