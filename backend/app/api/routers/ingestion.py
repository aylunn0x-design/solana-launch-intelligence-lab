"""Ingestion job control and history.

Jobs are started as FastAPI background tasks. That is a deliberate choice over
Celery/RQ for this project: ingestion is a single-process, rate-limited,
infrequent operation, so a broker and worker pool would add infrastructure
without solving a problem we have. The tradeoff is recorded in ARCHITECTURE.md —
if ingestion ever needs to run across machines or survive a restart mid-job,
this is the piece to replace.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, get_session
from app.models import DataSource, IngestionJob, JobStatus
from app.pipeline.ingest import IngestionService
from app.providers.geckoterminal import MAX_NEW_POOLS_PAGE
from app.providers.registry import build_primary_provider

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


class JobRequest(BaseModel):
    job_type: Literal["discover_pools", "backfill_ohlcv"] = "discover_pools"
    feed: Literal["new", "top", "trending"] = "new"
    pages: int = Field(default=1, ge=1, le=20)
    timeframe: Literal["minute", "hour", "day"] = "hour"
    limit_pools: int = Field(default=25, ge=1, le=200)
    min_age_minutes: int = Field(default=60, ge=0)


@router.get("/jobs")
def list_jobs(
    session: Session = Depends(get_session),
    status: JobStatus | None = None,
    job_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    stmt = select(IngestionJob).order_by(IngestionJob.started_at.desc())
    if status is not None:
        stmt = stmt.where(IngestionJob.status == status)
    if job_type:
        stmt = stmt.where(IngestionJob.job_type == job_type)

    jobs = list(session.execute(stmt.limit(limit)).scalars())
    return {
        "items": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status.value if hasattr(j.status, "value") else str(j.status),
                "params": j.params,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "duration_seconds": j.duration_seconds,
                "records_written": j.records_written,
                "records_skipped": j.records_skipped,
                "http_requests_made": j.http_requests_made,
                "rate_limit_hits": j.rate_limit_hits,
                "error_message": j.error_message,
                "notes": j.notes,
            }
            for j in jobs
        ],
        "count": len(jobs),
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No ingestion job {job_id}.")
    source = session.get(DataSource, job.source_id) if job.source_id else None
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "source": source.name if source else None,
        "params": job.params,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_seconds": job.duration_seconds,
        "records_requested": job.records_requested,
        "records_written": job.records_written,
        "records_skipped": job.records_skipped,
        "http_requests_made": job.http_requests_made,
        "rate_limit_hits": job.rate_limit_hits,
        "error_message": job.error_message,
        "notes": job.notes,
    }


async def _run_discover(feed: str, pages: int) -> None:
    settings = get_settings()
    provider = build_primary_provider(settings)
    try:
        with SessionLocal() as session:
            service = IngestionService(session, settings)
            await service.discover_pools(provider, feed=feed, pages=pages)
    finally:
        await provider.aclose()


async def _run_backfill(timeframe: str, limit_pools: int, min_age_minutes: int) -> None:
    from datetime import datetime, timedelta, timezone

    from app.models import Pool

    settings = get_settings()
    provider = build_primary_provider(settings)
    try:
        with SessionLocal() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age_minutes)
            addresses = list(
                session.execute(
                    select(Pool.pool_address)
                    .where(
                        Pool.pool_created_at.is_not(None),
                        Pool.pool_created_at <= cutoff,
                    )
                    .order_by(Pool.pool_created_at.asc())
                    .limit(limit_pools)
                ).scalars()
            )
            if not addresses:
                return
            service = IngestionService(session, settings)
            await service.backfill_ohlcv(provider, addresses, timeframe=timeframe)
    finally:
        await provider.aclose()


@router.post("/jobs", status_code=202)
def create_job(
    body: JobRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    """Queue an ingestion job.

    Returns 202 Accepted: the job runs in the background and its outcome is
    recorded in the ingestion_jobs table. Poll /api/ingestion/jobs for status.
    """
    if body.job_type == "discover_pools":
        if body.feed == "new" and body.pages > MAX_NEW_POOLS_PAGE:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The new-pools feed supports at most {MAX_NEW_POOLS_PAGE} pages "
                    f"(the provider returns HTTP 401 beyond that). Requested {body.pages}."
                ),
            )
        background.add_task(_run_discover, body.feed, body.pages)
        queued = {"job_type": "discover_pools", "feed": body.feed, "pages": body.pages}
    else:
        background.add_task(
            _run_backfill, body.timeframe, body.limit_pools, body.min_age_minutes
        )
        queued = {
            "job_type": "backfill_ohlcv",
            "timeframe": body.timeframe,
            "limit_pools": body.limit_pools,
        }

    running = session.scalar(
        select(IngestionJob.id)
        .where(IngestionJob.status == JobStatus.RUNNING)
        .limit(1)
    )
    warning = None
    if running is not None:
        warning = (
            f"Ingestion job {running} is still marked RUNNING. Concurrent jobs share the "
            "provider rate limit and will throttle each other."
        )

    return {
        "accepted": True,
        "queued": queued,
        "warning": warning,
        "note": (
            "The job runs in the background. Its status, row counts and any error are "
            "recorded against a row in ingestion_jobs; poll /api/ingestion/jobs."
        ),
    }


@router.get("/rate-limits")
def rate_limits() -> dict:
    """Configured rate limits and what is actually known about provider limits."""
    settings = get_settings()
    return {
        "configured_requests_per_minute": {
            "geckoterminal": settings.geckoterminal_rate_limit_per_min,
            "dexscreener": settings.dexscreener_rate_limit_per_min,
            "solana_rpc": settings.solana_rpc_rate_limit_per_min,
        },
        "documented_limits": "unknown",
        "explanation": (
            "The providers' documented free-tier limits could not be verified: the "
            "GeckoTerminal marketing site refused our client and the DexScreener docs "
            "render client-side. These values were set from observed behaviour (HTTP 429 "
            "after a small burst) and the client backs off adaptively on 429, reducing "
            "its own rate and recovering after sustained clean requests."
        ),
        "behaviour_on_429": (
            "Exponential backoff with jitter, honouring Retry-After; after 3 consecutive "
            "429s the client halves its rate (floor 5/min), and recovers gradually after "
            "25 consecutive clean requests."
        ),
    }
