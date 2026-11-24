# Contributing

## Research-integrity rules

These are not style preferences. A change that breaks one of them is wrong even
if the tests pass.

1. **Never fabricate data.** No sample data in production paths, no placeholder
   metrics, no "demo mode" that substitutes numbers when an API call fails. If
   data is unavailable, the UI says so.
2. **Missing is not zero.** A failed parse yields `NULL`, never `0`. A zero
   observation and an absent one mean different things.
3. **Never swallow an error.** A failed ingestion marks its job `failed` with
   the message attached. A failed API call renders an error state with the real
   reason.
4. **Refuse rather than guess.** Below the documented thresholds the platform
   declines to report a statistic. Lowering a threshold to make a page look
   fuller is a research-integrity bug.
5. **Keep the leakage boundary intact.** New features must be registered with an
   `availability`. Only `at_launch` features may be model inputs; the gate
   enforces this and must not be bypassed.
6. **Caveats travel with results.** Any new statistical output carries its
   sample size, missing-data share, method and warnings.
7. **Correlation is never described as causation**, in code comments, UI copy
   or documentation.
8. **Dataset versions are immutable.** Do not add an "overwrite" flag.

## Conventions

**Python** — 3.13, `ruff` clean, type hints on public functions. Docstrings
explain *why*, not what. Database access through SQLAlchemy; never build SQL by
string interpolation.

**TypeScript** — strict mode, `eslint` and `tsc --noEmit` clean. Server
components by default; `"use client"` only where interaction requires it.
Remember that functions cannot cross the RSC boundary — pass a serializable
descriptor instead. Validate every API response with Zod.

**Tests** — no test may touch the network; mock the transport with `respx`.
Statistical tests assert against independently-known answers, not against
current output. Add a test for each refusal path as well as each success path.

## Workflow

```bash
# Backend
cd backend && PYTHONPATH=. ./.venv/bin/python -m pytest -q
cd backend && ./.venv/bin/ruff check app tests

# Frontend
cd frontend && npx vitest run && npx eslint . && npx tsc --noEmit

# Schema change: edit models, then
cd backend && PYTHONPATH=. ./.venv/bin/alembic revision --autogenerate -m "what changed"
# review the generated file before committing — autogenerate is a draft
```

CI runs all of the above plus a migration drift check: if the models and
migrations disagree, the build fails.

## Adding a data source

1. Implement a provider in `app/providers/` returning the normalised
   `PoolSnapshot` / `Candle` dataclasses.
2. Register it in `app/providers/registry.py` with an honest description,
   including its limitations.
3. Add tests with payload shapes copied from a real response.
4. Record in `DATA_SOURCES.md` what you verified **by live request** versus what
   you took from documentation.
