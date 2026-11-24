# Solana Launch Intelligence Lab

A research platform for studying what happens to newly launched Solana tokens
after they launch, built on real on-chain market data.

**Research question.** Which observable characteristics of newly launched Solana
tokens are associated with continued liquidity, market activity and survival,
and how robust are those relationships across market conditions?

> **This is a research instrument, not a trading system.** It produces
> associations measured in observational data. It contains no trading
> functionality, no buy/sell signals, and makes no predictive claims. Nothing
> here is financial advice.

---

## What it actually does

- Ingests live pool data from two free APIs (no keys required) with provenance
  and raw-payload retention, so every number can be traced to the response it
  came from.
- Builds **immutable dataset versions** with a documented feature registry and a
  structural anti-leakage boundary.
- Runs descriptive statistics, correlation analysis with multiple-comparison
  correction, Kaplan-Meier survival analysis, Cox proportional hazards, and
  baseline-vs-model comparison — each reporting sample size, missing-data share,
  effect size, confidence intervals and limitations.
- **Refuses to produce a result it cannot support.** With no observed events the
  survival curve is not drawn; with too few events the Cox model is not fitted;
  below a minimum sample no inferential statistic is reported. These refusals
  are the feature, not a gap.

### What it does not do

- It does not collect holder-concentration data. The public Solana RPC
  rate-limits `getTokenLargestAccounts` (HTTP 429 on the *first* call) and caps
  it at 20 accounts, so a true holder count cannot be derived from it. Research
  question 2 is documented as blocked rather than quietly dropped. See
  [DATA_SOURCES.md](DATA_SOURCES.md) §4.
- It ships no sample or demo data. An empty database renders an empty state with
  instructions, never plausible-looking numbers.

---

## Screenshots

Replace these with real captures once you have run the stack
(`docs/screenshots/` is a convention, not a requirement).

| View | File |
|---|---|
| Overview — dataset size, coverage, warnings | `docs/screenshots/overview.png` |
| Token Explorer — filterable pool table | `docs/screenshots/explorer.png` |
| Research Lab — distributions, cohort comparison | `docs/screenshots/research.png` |
| Correlation — matrix with FDR-adjusted p-values | `docs/screenshots/correlation.png` |
| Survival — Kaplan-Meier, Cox, threshold sensitivity | `docs/screenshots/survival.png` |
| Data Status — health checks and job history | `docs/screenshots/data-status.png` |

---

## Architecture

```
GeckoTerminal API ─┐
DexScreener API   ─┼─► provider layer ─► validation ─► normalisation ─► Postgres/SQLite
Solana RPC (off)  ─┘   (rate limit,        (reject/        (domain          │
                        retry, backoff)     flag)           objects)        │
                                                                            ▼
                                                            feature engineering
                                                          (leakage-classified)
                                                                            │
                                                                            ▼
                                                           immutable dataset version
                                                                            │
                                                                            ▼
                               FastAPI  ◄──  research engine (stats, survival, models)
                                  │
                                  ▼
                        Next.js research terminal
```

Full reasoning and the tradeoffs behind each choice: [ARCHITECTURE.md](ARCHITECTURE.md).

### Tech stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind CSS 4, Recharts, Zod |
| Backend | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Analysis | pandas, NumPy, SciPy, scikit-learn, statsmodels, lifelines |
| Database | PostgreSQL (production), SQLite (zero-setup local) |
| Infra | Docker, Docker Compose, GitHub Actions |

---

## Local setup

### Requirements

Python 3.13+, Node.js 22+. No database server is required for local use — the
default is SQLite.

### 1. Backend

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
```

Create the schema and register the data sources:

```bash
cd backend
PYTHONPATH=. ./.venv/bin/alembic upgrade head
```

> `python -m app.cli init-db` creates the same tables directly from the models.
> It is a convenience for throwaway local databases; use Alembic anywhere the
> schema needs to be migrated rather than recreated.

### 2. Collect real data

```bash
cd backend
export PYTHONPATH=.

# Unbiased inception cohort: every launch in the last ~6 minutes
./.venv/bin/python -m app.cli ingest --feed new --pages 10

# Pools that already have history (survivorship-biased, labelled as such)
./.venv/bin/python -m app.cli ingest --feed top --pages 5

# Real OHLCV history for pools old enough to have it
./.venv/bin/python -m app.cli backfill --timeframe hour --limit-pools 50 \
    --min-age-minutes 90 --max-age-days 30 --order oldest --only-missing

# Compute features into an immutable dataset version
./.venv/bin/python -m app.cli build-dataset --label v1 --inactivity-hours 24

./.venv/bin/python -m app.cli status
```

**The inception cohort has to accumulate.** The new-pools feed covers only the
most recent few minutes and cannot be paged back, so run `scripts/collect.sh` on
a schedule to build an unbiased sample over time:

```bash
*/5 * * * * /path/to/scripts/collect.sh >> /var/log/solana-lab.log 2>&1
```

### 3. Run the API

```bash
cd backend
PYTHONPATH=. ./.venv/bin/uvicorn app.main:app --reload
```

API docs: <http://127.0.0.1:8000/docs>

### 4. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>.

---

## Environment variables

Copy `.env.example` to `.env`. No secrets are committed; `.env` is gitignored.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./solana_lab.db` | SQLAlchemy URL; `postgresql+psycopg://…` for Postgres |
| `ENVIRONMENT` | `development` | `production` disables the interactive API docs |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed frontend origins |
| `GECKOTERMINAL_RATE_LIMIT_PER_MIN` | `25` | Primary source request budget |
| `DEXSCREENER_RATE_LIMIT_PER_MIN` | `50` | Secondary source request budget |
| `ENABLE_HOLDER_COLLECTION` | `false` | Needs a dedicated RPC provider to be useful |
| `SOLANA_RPC_URL` | public endpoint | Point at a dedicated provider to enable holder data |
| `DEFAULT_INACTIVITY_THRESHOLD_HOURS` | `24` | Default survival definition |
| `MIN_CANDLES_FOR_ANALYSIS` | `6` | Minimum observations to enter a dataset |
| `NEXT_PUBLIC_API_BASE_URL` | `http://127.0.0.1:8000` | Where the frontend finds the API |

---

## Testing

```bash
# Backend — 219 tests, no network access (providers are mocked with respx)
cd backend && PYTHONPATH=. ./.venv/bin/python -m pytest -q
cd backend && ./.venv/bin/ruff check app tests

# Frontend — 25 tests
cd frontend && npx vitest run
cd frontend && npx eslint . && npx tsc --noEmit
```

The suite covers parsing and unit coercion, provider behaviour under HTTP 429,
validation rules, ingestion idempotency, the survival/censoring logic, the
anti-leakage gate, every statistical method, and the API's status codes and
empty states.

---

## Deployment

```bash
cp .env.example .env     # set POSTGRES_PASSWORD
docker compose up --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.cli ingest --feed new --pages 10
```

> **Note on verification:** the Compose stack and Dockerfiles were written and
> reviewed but **could not be executed** in the environment this project was
> built in (no Docker daemon available). The PostgreSQL path *was* verified
> directly against a real PostgreSQL 16 server: migrations apply, reverse and
> re-apply with no drift, and a full ingest → dataset build cycle completed
> against it. Treat the container build as untested until you run it.

Hosting options: the backend runs anywhere that can run a container (Fly.io,
Railway, Render); the frontend deploys to Vercel with
`NEXT_PUBLIC_API_BASE_URL` pointed at the backend. Neither data source requires
a paid key.

---

## API

Interactive docs at `/docs` in development. Main endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness plus a real database round-trip |
| `GET /api/overview` | Dataset size, coverage, freshness, warnings |
| `GET /api/tokens` | Paginated pool list with filters and sorting |
| `GET /api/tokens/{id}` | Pool detail, provenance, missing-data warnings |
| `GET /api/tokens/{id}/history` | Liquidity panel and OHLCV series |
| `GET /api/datasets` · `POST /api/datasets` | List / build dataset versions |
| `GET /api/research/summary` | Variables, leakage classes, cohort breakdown |
| `GET /api/research/describe` · `/histogram` | Descriptive statistics, distributions |
| `GET /api/research/correlation[/matrix]` | Correlation with CI and FDR adjustment |
| `GET /api/research/compare-groups` | Mann-Whitney / Welch group comparison |
| `GET /api/research/survival[/cox,/sensitivity]` | Kaplan-Meier, Cox, RQ6 sensitivity |
| `POST /api/research/model` | Baseline vs logistic vs random forest |
| `GET /api/experiments` · `POST /api/experiments` | Experiment tracking |
| `GET /api/ingestion/jobs` · `POST /api/ingestion/jobs` | Job history and control |
| `GET /api/data-quality` | Completeness, duplicates, invalid rows, job health |

---

## Research methodology

Summarised here; stated in full in [RESEARCH_PLAN.md](RESEARCH_PLAN.md) and on
the platform's own Methodology page.

**Unit of observation** is a *pool*, not a token: a launch is the creation of an
AMM market, and a token with several pools has several lifecycles.

**Survival** is measured on trading activity, which is what on-chain data can
observe. A pool is "dead" once hourly volume stays at zero for longer than the
dataset's inactivity threshold; otherwise it is **right-censored**. Binary
survival labels are three-valued: a pool still alive but younger than the
horizon is *undetermined*, never a failure — labelling it `false` would make the
survival rate a function of when collection started.

**The leakage boundary is structural.** Every feature is registered as
known-at-launch, forward-looking or an outcome, and passing anything but a
launch-time feature to a model raises an error. The train/test split is
chronological.

---

## Known limitations

These are real and they bound what the platform can claim.

1. **Survivorship bias in retrospective samples.** The new-pools feed cannot be
   paged back, so an unbiased cohort can only be accumulated over time. Pools
   found through ranked "top"/"trending" listings are present *because* they are
   currently notable, so survival rates over them are biased upward. Every pool
   records how it was discovered and every result carries the cohort mix.
2. **Holder concentration unavailable** (see above). RQ2 is out of scope in the
   MVP.
3. **Short observation windows** cannot speak to market regimes (RQ5). Results
   warn when launch dates span less than a week.
4. **Heavy censoring.** When most pools are still active the survival curve's
   tail is extrapolation, and the platform says so.
5. **Sub-hour lifecycles are invisible.** Many launches live minutes; the hourly
   candle grid plus the minimum-candle rule excludes exactly the shortest-lived
   pools. This biases the sample toward longer-lived tokens and more data does
   not fix it.
6. **Degenerate liquidity features.** Until the refresh job has polled a pool
   repeatedly, its "peak" liquidity equals its initial liquidity (they correlate
   at 1.0 by construction) and its coefficient of variation is undefined. The
   platform detects this and warns.
7. **Reported activity only.** Wash trading is not detectable from this data.
8. **Provider terms not verified.** See [DATA_SOURCES.md](DATA_SOURCES.md) §6
   before publishing a derived dataset.

---

## Roadmap

- **P1** — accumulate a multi-week inception cohort so survival analysis runs on
  an unbiased sample; add the liquidity-variability panel that repeated polling
  enables.
- **P2** — clustering (k-means/hierarchical) and PCA over launch-time features;
  per-experiment model comparison stored in the database.
- **P3** — a dedicated RPC provider to unblock holder concentration (RQ2);
  market-regime segmentation (RQ5) once the window is long enough; a second
  chain for external validity.

---

## Documentation

| File | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Structure, data flow, decisions and tradeoffs |
| [RESEARCH_PLAN.md](RESEARCH_PLAN.md) | Hypotheses, variables, methods, confounders |
| [DATA_DICTIONARY.md](DATA_DICTIONARY.md) | Every table and field, with types and sources |
| [DATA_SOURCES.md](DATA_SOURCES.md) | What was verified live vs. assumed |
| [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) | What was built, what broke, what was learned |
| [TODO.md](TODO.md) | Prioritised outstanding work |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Conventions and the research-integrity rules |

## Licence

No licence has been chosen yet. Until one is added, default copyright applies.
