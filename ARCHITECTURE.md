# Architecture

How the system is put together, and why each significant choice was made. The
tradeoffs are stated because several of them are defensible either way.

---

## 1. Shape of the system

```
backend/
  app/
    core/        config (env-driven), database engine/session
    providers/   one module per data source + shared rate-limited HTTP client
    pipeline/    ingestion orchestration, validation, feature engineering
    research/    statistics, survival analysis, modelling
    api/routers/ FastAPI routers, one per domain area
    cli.py       every pipeline operation, runnable from a terminal
  alembic/       migrations
  tests/         219 tests, no network access
frontend/
  app/           App Router pages (server components by default)
  components/    design-system primitives, charts, filters
  lib/           API client with Zod validation, formatting
  tests/         25 tests
scripts/collect.sh   one scheduled collection cycle
```

### Data flow

```
SOURCE → RAW INGESTION → RAW STORAGE → VALIDATION → NORMALISATION
       → ANALYTICAL TABLES → FEATURE ENGINEERING → DATASET VERSION → RESEARCH
```

Each stage is a separate module so a failure can be attributed to a stage, and
so the raw bytes survive every transformation after them.

---

## 2. Decisions and tradeoffs

### 2.1 The unit of observation is a pool, not a token

A "launch" is the creation of an AMM market. A token can have several pools —
different venues, different quote assets — and each has its own liquidity and
its own lifecycle. Keying the study on tokens would force an arbitrary merge of
several market lifetimes into one.

*Tradeoff:* a token with multiple pools contributes several rows, so pool-level
rows are not independent observations of distinct tokens. The schema keeps
`token_id` on every pool so this can be modelled or clustered later; the MVP
reports it as a known limitation rather than pretending independence.

### 2.2 Provider abstraction, with normalised domain objects

Providers translate their own wire formats into `PoolSnapshot` / `Candle`
dataclasses. The pipeline and schema never reference a vendor's field names, so
adding a source means writing a translation, not changing the database.

This paid for itself immediately: GeckoTerminal sends `pool_created_at` as an
ISO-8601 string while DexScreener sends `pairCreatedAt` as epoch
**milliseconds**. One parsing helper absorbs both, and a test pins the
behaviour.

*Alternative considered:* storing each provider's raw JSON and parsing at query
time. Rejected — it moves parsing bugs from ingestion (where they are caught by
tests once) to every read path.

### 2.3 Rate limiting is a first-class concern, not error handling

Measured behaviour during development: GeckoTerminal returned HTTP 429 after
roughly six requests in a minute, and the public Solana RPC returned 429 on the
*first* call to an expensive method. Throttling is the normal case here.

`providers/base.py` implements a token-bucket limiter with exponential backoff
and jitter, honours `Retry-After`, and **adapts**: three consecutive 429s halve
the client's own rate (floor 5/min), and 25 consecutive clean requests let it
recover toward the configured rate.

The recovery half matters. An early burst of 429s during a long backfill would
otherwise cripple throughput for the entire run — observed in practice before
recovery was added, when the limiter dropped 25 → 12 → 6 req/min and stayed
there.

*Tradeoff:* the limiter is in-process, so two concurrent ingestion processes
would each think they own the full budget. A distributed limiter (Redis) would
fix that, but this project runs ingestion as a single scheduled process, so it
would be infrastructure without a problem to solve. The API warns when a second
job is queued while one is running.

### 2.4 Raw payloads are retained

`raw_payloads` stores the unmodified response, the endpoint, the parameters and
the HTTP status for every call that produced rows. Normalised values can
therefore be re-derived from the original bytes.

*Tradeoff:* storage grows faster than the analytical tables. Acceptable: this is
the difference between "a number in a database" and reproducible research, and
the volume is modest at research scale.

### 2.5 Discovery method is permanent

How a pool entered the dataset (`new_pools_feed`, `top_pools`, `trending_pools`,
`manual`) is written once and never overwritten, even when a later job sees the
same pool through a different feed. It is a property of the *sample*, not of the
pool, and changing it would corrupt the bias accounting that every analysis
depends on. A test pins this.

### 2.6 Dataset versions are immutable

Features are computed into a `dataset_versions` row with its criteria, its
inactivity threshold and its cohort composition recorded. Rebuilding under an
existing label is refused.

If features were recomputed in place, every experiment referencing that label
would silently change meaning while its recorded numbers stayed put. Immutable
versions are what make a recorded result reproducible.

*Tradeoff:* disk usage grows with each version, and the user has to pick labels.
Worth it.

### 2.7 The leakage boundary is enforced in code

Each feature is registered with an `availability`: `AT_LAUNCH`,
`FORWARD_LOOKING` or `OUTCOME`. `model_input_features()` derives the legal set
from that registry, and `assert_no_leakage()` raises on anything else. The Cox
model and the classifiers both call it; the API turns the error into a clear
`leakage_blocked` response rather than a 500.

A code-level gate rather than a documented convention, because conventions are
exactly what gets forgotten six months later.

### 2.8 SQLite and PostgreSQL behind the same code

SQLite is the default so the project runs with no infrastructure; PostgreSQL is
the production target. Both go through SQLAlchemy, Alembic runs `render_as_batch`
on SQLite (which cannot `ALTER` in place), and SQLite gets `PRAGMA foreign_keys=ON`
— without it, SQLite silently ignores foreign keys, so referential integrity
would hold in production and not locally.

Verified on both: migrations apply, reverse and re-apply with no drift, and a
full ingest → dataset-build cycle ran against a real PostgreSQL 16 server.

### 2.9 Ingestion runs as a FastAPI background task

Rather than Celery/RQ with a broker and worker pool. Ingestion is single-process,
rate-limited and infrequent; a job queue would add two services and a failure
mode without solving a problem this project has. The job row in
`ingestion_jobs` is the durable record.

*The tradeoff, stated plainly:* a job interrupted by a restart stays `RUNNING`
forever, and jobs cannot be distributed. If ingestion ever needs to survive
restarts mid-job or run across machines, this is the piece to replace — nothing
else depends on how the job was started.

### 2.10 Server components by default

Next.js App Router pages fetch on the server, so the browser never holds an API
base URL it must authenticate to, and the analysis pages render fully formed.
Client components are used only where interaction demands it: filters, the
sidebar, chart rendering.

Analysis selections live in the **URL**, not component state. A research result
is only reproducible if the exact view that produced it can be linked to and
reopened.

*A bug this caused, and the fix:* server components cannot pass functions across
the RSC boundary, so passing `valueFormatter={(v) => usd(v)}` into a
`"use client"` chart threw at runtime. Charts now take a serializable format
*name* (`valueFormat="usd"`) and resolve it to a function on the client.

### 2.11 Responses are validated with Zod before rendering

The backend is deployed separately and can drift. A missing field would
otherwise render as a confident-looking blank. Validation failure produces a
`SchemaError` naming the offending fields.

**A failed request is never replaced with sample data.** There is no demo mode.
This is the project's central rule and it is pinned by a test.

### 2.12 No chart library beyond Recharts; no table library at all

TanStack Table was considered and rejected: sorting, filtering and pagination
all happen in SQL on the server (the dataset grows without bound, so paging it
client-side would not scale and would make "total" a lie). With the server doing
that work, the table is a `<table>` element, and a library would add a
dependency for styling.

---

## 3. Database schema

Thirteen tables in three groups.

**Provenance** — `data_sources`, `ingestion_jobs`, `raw_payloads`.
**Core entities** — `tokens`, `pools`, `market_observations`, `ohlcv_candles`,
`holder_snapshots`.
**Research** — `feature_definitions`, `dataset_versions`, `pool_features`,
`research_experiments`, `experiment_results`.

Integrity is enforced by the database, not by application code:

| Constraint | Prevents |
|---|---|
| `uq_obs_pool_source_time` on `(pool_id, source_id, observed_at)` | A source recording two states for one pool at one instant |
| `uq_candle_pool_tf_ts` on `(pool_id, timeframe, ts)` | Duplicate candles on re-backfill |
| `ck_obs_liq_nonneg`, `ck_candle_vol_nonneg` | Negative liquidity or volume reaching storage |
| FKs with `ON DELETE CASCADE` | Orphan observations after a pool is removed |

Storing one row **per source** rather than merging is what makes cross-source
disagreement measurable — that is how a provider silently changing units gets
detected.

Full field-level documentation: [DATA_DICTIONARY.md](DATA_DICTIONARY.md).

---

## 4. Security

- Secrets come from the environment; `.env` is gitignored and `.env.example`
  contains only placeholders. `alembic.ini` deliberately holds no URL — `env.py`
  reads it from settings.
- All queries go through SQLAlchemy with bound parameters. The explorer's search
  uses a parameterised `ILIKE`; no user input is interpolated into SQL.
- Inputs are validated by Pydantic models and typed query parameters, so a bad
  request is a 422 rather than an exception.
- CORS is an explicit allow-list from `CORS_ORIGINS`, methods limited to GET and
  POST, credentials off.
- Errors return the reason without leaking internals: `LookupError` → 404,
  `ValueError` → 400. Interactive API docs are disabled when
  `ENVIRONMENT=production`.
- Containers run as non-root users.
- No authentication exists, because the API is read-mostly research data with no
  per-user state. Adding auth would be complexity without a use case. **If this
  is ever deployed publicly, the ingestion endpoints should be protected** —
  they consume a shared rate-limit budget.

---

## 5. Testing strategy

Backend (219 tests): parsing and unit coercion; provider behaviour against
recorded payload shapes and under HTTP 429/5xx/4xx; validation rules; ingestion
idempotency and job accounting; the survival and censoring logic; the
anti-leakage gate; every statistical method; and the API's status codes, empty
states and refusals.

Frontend (25 tests): formatting (missing renders as an em dash, never 0) and the
API client's error behaviour, including that it never substitutes sample data.

**No test touches the network.** Provider tests mock the HTTP transport with
`respx`, using payload shapes copied from real responses. A live dependency
would make CI flaky and hammer a free tier on every push.

Statistical tests check against independently-known answers — a perfect
monotonic relationship, a hand-computed coefficient of variation, a known
drawdown — rather than against whatever the code happens to return. Several
assert that the platform *refuses* to report a result, which matters as much as
computing one correctly.
