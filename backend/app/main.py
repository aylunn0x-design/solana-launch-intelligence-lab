"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import (
    data_quality,
    datasets,
    experiments,
    health,
    ingestion,
    overview,
    research,
    tokens,
)
from app.core.config import get_settings

logging.basicConfig(
    level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

DESCRIPTION = """
Research API for the Solana Launch Intelligence Lab.

Studies the lifecycle of newly launched Solana tokens using historical on-chain
market data. Every number served by this API is computed from stored
observations; endpoints return an explicit "unavailable" state rather than
placeholder values when data is missing.

**This is a research instrument, not financial advice.** It produces
associations from observational data. It contains no trading functionality and
makes no predictive claims about future prices.
"""

app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version="0.1.0",
    # Interactive docs are useful in development; disabled in production unless
    # explicitly re-enabled, to avoid exposing the schema surface by default.
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(LookupError)
async def lookup_error_handler(_request: Request, exc: LookupError) -> JSONResponse:
    """Missing dataset/entity -> 404 with the actionable reason."""
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    """Bad analysis request -> 400 with the reason, not a 500."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for router in (
    health.router,
    overview.router,
    tokens.router,
    datasets.router,
    research.router,
    experiments.router,
    ingestion.router,
    data_quality.router,
):
    app.include_router(router, prefix="/api")
