"""Experiment tracking: record a research question, method, results, limitations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import DatasetVersion, ExperimentResult, ResearchExperiment

router = APIRouter(tags=["experiments"])


class ResultIn(BaseModel):
    metric_name: str = Field(min_length=1, max_length=64)
    metric_value: float | None = None
    metric_text: str | None = None
    sample_size: int | None = Field(default=None, ge=0)
    ci_lower: float | None = None
    ci_upper: float | None = None
    p_value: float | None = Field(default=None, ge=0, le=1)
    split: str | None = Field(default=None, max_length=32)
    details: dict | None = None


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    research_question: str = Field(min_length=1)
    hypothesis: str | None = None
    dataset_version_id: int | None = None
    method: str = Field(min_length=1, max_length=64)
    features_used: list[str] | None = None
    target_definition: str | None = None
    split_strategy: str | None = Field(default=None, max_length=64)
    parameters: dict | None = None
    notes: str | None = None
    limitations: str | None = None
    results: list[ResultIn] = Field(default_factory=list)


@router.get("/experiments")
def list_experiments(
    session: Session = Depends(get_session),
    dataset_version_id: int | None = None,
    method: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict:
    stmt = select(ResearchExperiment)
    if dataset_version_id is not None:
        stmt = stmt.where(ResearchExperiment.dataset_version_id == dataset_version_id)
    if method:
        stmt = stmt.where(ResearchExperiment.method == method)

    all_rows = list(session.execute(stmt.order_by(ResearchExperiment.created_at.desc())).scalars())
    total = len(all_rows)
    page_rows = all_rows[(page - 1) * page_size : page * page_size]

    return {
        "items": [_brief(e, session) for e in page_rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if page_size else 0,
        },
    }


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: int, session: Session = Depends(get_session)) -> dict:
    exp = session.get(ResearchExperiment, experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail=f"No experiment {experiment_id}.")
    return _detail(exp, session)


@router.post("/experiments", status_code=201)
def create_experiment(
    body: ExperimentIn, session: Session = Depends(get_session)
) -> dict:
    if body.dataset_version_id is not None:
        if session.get(DatasetVersion, body.dataset_version_id) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"dataset_version_id {body.dataset_version_id} does not exist. An "
                    "experiment must reference a real dataset version to be reproducible."
                ),
            )

    exp = ResearchExperiment(
        name=body.name,
        research_question=body.research_question,
        hypothesis=body.hypothesis,
        dataset_version_id=body.dataset_version_id,
        method=body.method,
        features_used=body.features_used,
        target_definition=body.target_definition,
        split_strategy=body.split_strategy,
        parameters=body.parameters,
        notes=body.notes,
        limitations=body.limitations,
    )
    session.add(exp)
    session.flush()

    for result in body.results:
        session.add(
            ExperimentResult(
                experiment_id=exp.id,
                metric_name=result.metric_name,
                metric_value=result.metric_value,
                metric_text=result.metric_text,
                sample_size=result.sample_size,
                ci_lower=result.ci_lower,
                ci_upper=result.ci_upper,
                p_value=result.p_value,
                split=result.split,
                details=result.details,
            )
        )
    session.commit()
    return _detail(exp, session)


@router.get("/experiments/compare/metrics")
def compare_experiments(
    session: Session = Depends(get_session),
    ids: list[int] = Query(..., description="Experiment ids to compare"),
) -> dict:
    """Side-by-side metric comparison across experiments."""
    experiments = list(
        session.execute(
            select(ResearchExperiment).where(ResearchExperiment.id.in_(ids))
        ).scalars()
    )
    if not experiments:
        raise HTTPException(status_code=404, detail="None of those experiment ids exist.")

    found_ids = {e.id for e in experiments}
    missing = [i for i in ids if i not in found_ids]

    metric_names: list[str] = []
    rows: dict[int, dict] = {}
    for exp in experiments:
        results = list(
            session.execute(
                select(ExperimentResult).where(ExperimentResult.experiment_id == exp.id)
            ).scalars()
        )
        rows[exp.id] = {r.metric_name: r for r in results}
        for r in results:
            if r.metric_name not in metric_names:
                metric_names.append(r.metric_name)

    comparison = []
    for metric in metric_names:
        entry: dict = {"metric_name": metric, "values": []}
        for exp in experiments:
            res = rows[exp.id].get(metric)
            entry["values"].append(
                {
                    "experiment_id": exp.id,
                    "experiment_name": exp.name,
                    "value": res.metric_value if res else None,
                    "text": res.metric_text if res else None,
                    "sample_size": res.sample_size if res else None,
                    "p_value": res.p_value if res else None,
                    "ci": [res.ci_lower, res.ci_upper] if res else None,
                    "present": res is not None,
                }
            )
        comparison.append(entry)

    warnings = []
    version_ids = {e.dataset_version_id for e in experiments}
    if len(version_ids) > 1:
        warnings.append(
            "These experiments use different dataset versions, so their metrics are not "
            "directly comparable — differences may come from the data, not the method."
        )
    if missing:
        warnings.append(f"Experiment id(s) not found: {missing}.")

    return {
        "experiments": [_brief(e, session) for e in experiments],
        "metrics": comparison,
        "warnings": warnings,
    }


def _brief(exp: ResearchExperiment, session: Session) -> dict:
    result_count = len(
        list(
            session.execute(
                select(ExperimentResult.id).where(ExperimentResult.experiment_id == exp.id)
            ).scalars()
        )
    )
    return {
        "id": exp.id,
        "name": exp.name,
        "research_question": exp.research_question,
        "method": exp.method,
        "dataset_version_id": exp.dataset_version_id,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "result_count": result_count,
    }


def _detail(exp: ResearchExperiment, session: Session) -> dict:
    results = list(
        session.execute(
            select(ExperimentResult)
            .where(ExperimentResult.experiment_id == exp.id)
            .order_by(ExperimentResult.metric_name)
        ).scalars()
    )
    version = (
        session.get(DatasetVersion, exp.dataset_version_id)
        if exp.dataset_version_id
        else None
    )
    return {
        "id": exp.id,
        "name": exp.name,
        "research_question": exp.research_question,
        "hypothesis": exp.hypothesis,
        "method": exp.method,
        "features_used": exp.features_used,
        "target_definition": exp.target_definition,
        "split_strategy": exp.split_strategy,
        "parameters": exp.parameters,
        "notes": exp.notes,
        "limitations": exp.limitations,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "dataset_version": (
            {
                "id": version.id,
                "label": version.label,
                "pool_count": version.pool_count,
                "inactivity_threshold_hours": version.inactivity_threshold_hours,
                "inception_cohort_share": version.inception_cohort_share,
                "criteria": version.criteria,
            }
            if version
            else None
        ),
        "results": [
            {
                "metric_name": r.metric_name,
                "metric_value": r.metric_value,
                "metric_text": r.metric_text,
                "sample_size": r.sample_size,
                "ci_lower": r.ci_lower,
                "ci_upper": r.ci_upper,
                "p_value": r.p_value,
                "split": r.split,
                "details": r.details,
            }
            for r in results
        ],
        "reproducibility": {
            "dataset_version_id": exp.dataset_version_id,
            "note": (
                "To reproduce: load this dataset version and re-run the recorded method "
                "with the recorded parameters. Dataset versions are immutable."
            ),
        },
    }
