"""Dataset versions: immutable analytical snapshots."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import DatasetVersion, PoolFeature
from app.pipeline.features import build_dataset_version

router = APIRouter(tags=["datasets"])


@router.get("/datasets")
def list_datasets(session: Session = Depends(get_session)) -> dict:
    versions = session.execute(
        select(DatasetVersion).order_by(DatasetVersion.built_at.desc())
    ).scalars().all()
    return {
        "items": [_summary(v) for v in versions],
        "count": len(versions),
        "note": (
            "Dataset versions are immutable: rebuilding uses a new label so an "
            "experiment's inputs can always be recovered."
        ),
    }


@router.get("/datasets/{version_id}")
def get_dataset(version_id: int, session: Session = Depends(get_session)) -> dict:
    version = session.get(DatasetVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"No dataset version {version_id}.")

    feature_rows = session.scalar(
        select(func.count())
        .select_from(PoolFeature)
        .where(PoolFeature.dataset_version_id == version_id)
    ) or 0

    completeness = session.execute(
        select(
            func.avg(PoolFeature.completeness),
            func.avg(PoolFeature.candle_count),
            func.avg(PoolFeature.observation_count),
            func.sum(func.coalesce(PoolFeature.event_observed, 0)),
        ).where(PoolFeature.dataset_version_id == version_id)
    ).one()

    return {
        **_summary(version),
        "feature_rows": feature_rows,
        "mean_completeness": _f(completeness[0]),
        "mean_candles_per_pool": _f(completeness[1]),
        "mean_observations_per_pool": _f(completeness[2]),
        "events_observed": int(completeness[3] or 0),
        "censored": feature_rows - int(completeness[3] or 0),
    }


class BuildDatasetRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    cohort: str = Field(default="all", pattern="^(all|inception|retrospective)$")
    inactivity_threshold_hours: int = Field(default=24, ge=1, le=720)
    min_candles: int = Field(default=6, ge=1, le=1000)
    description: str | None = None


@router.post("/datasets", status_code=201)
def create_dataset(
    body: BuildDatasetRequest, session: Session = Depends(get_session)
) -> dict:
    """Build a dataset version by computing features over eligible pools."""
    try:
        version = build_dataset_version(
            session,
            label=body.label,
            cohort=body.cohort,
            inactivity_threshold_hours=body.inactivity_threshold_hours,
            min_candles=body.min_candles,
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    payload = _summary(version)
    if version.pool_count == 0:
        payload["warning"] = (
            "The dataset was created but contains no pools: no pool met the inclusion "
            f"criteria (needs a launch timestamp and at least {body.min_candles} hourly "
            "candles). Run the OHLCV backfill job first."
        )
    return payload


def _summary(version: DatasetVersion) -> dict:
    return {
        "id": version.id,
        "label": version.label,
        "description": version.description,
        "pool_count": version.pool_count,
        "observation_count": version.observation_count,
        "candle_count": version.candle_count,
        "inception_cohort_share": version.inception_cohort_share,
        "inactivity_threshold_hours": version.inactivity_threshold_hours,
        "criteria": version.criteria,
        "built_at": version.built_at.isoformat() if version.built_at else None,
    }


def _f(value) -> float | None:
    return float(value) if value is not None else None
