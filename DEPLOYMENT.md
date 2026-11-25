# Deployment

The stack is split across two hosts because it has to be:

```
Vercel  ──►  Render (FastAPI container)  ──►  PostgreSQL
frontend          backend + research engine
```

**Why the backend is not on Vercel.** Its dependencies total ~521 MB — scipy
(111 MB), pandas (53 MB), scikit-learn (46 MB), statsmodels (38 MB), numpy
(35 MB) — against Vercel's 250 MB serverless limit. scipy + numpy + pandas
alone is ~251 MB, so even a survival-analysis-only build does not fit. It runs
as a container instead. This is measured, not estimated.

---

## 1. Backend — Render

A `render.yaml` blueprint at the repository root declares the web service and a
PostgreSQL database together.

1. Go to <https://dashboard.render.com/blueprints> → **New Blueprint Instance**.
2. Select this repository. Render reads `render.yaml` and shows two resources:
   `solana-lab-api` and `solana-lab-db`.
3. It will prompt for `CORS_ORIGINS` — the one value the blueprint deliberately
   does not guess. Set it to your Vercel URL once you have it (step 2); for the
   first deploy `http://localhost:3000` is fine, and you can edit it after.
4. Deploy. The container applies Alembic migrations on start
   (`RUN_MIGRATIONS_ON_START=true`) and then serves on Render's `$PORT`.
5. Check `https://<your-service>.onrender.com/api/health` — it should report
   `"status": "ok"` and `"database": {"reachable": true}`.

At this point the API is live but the database is **empty**, so the frontend
will correctly show its empty state. Populate it in step 3.

### Two free-tier caveats, stated plainly

- **The service sleeps after 15 minutes idle** and takes ~50 s to wake. The
  first page load after a quiet spell is slow. Paid plans do not sleep.
- **A free Render Postgres is deleted after 30 days.** For anything you intend
  to keep, create a database somewhere with a durable free tier and point
  `DATABASE_URL` at it instead:
  - **Neon** (<https://neon.tech>) — free tier does not expire. Recommended.
  - **Supabase** — fine if you have a free project slot available.

  Then in Render: delete the `DATABASE_URL` entry that is wired
  `fromDatabase`, add a plain `DATABASE_URL` with the provider's connection
  string, and remove the `solana-lab-db` resource. A plain `postgres://` URL is
  accepted — the app rewrites it to the driver SQLAlchemy 2 needs.

---

## 2. Frontend — Vercel

1. <https://vercel.com/new> → import this repository.
2. Set **Root Directory** to `frontend`. Vercel detects Next.js; leave the
   build and output settings alone.
3. Add one environment variable:

   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | `https://<your-service>.onrender.com` |

   No trailing slash. This is read at build time *and* at request time by the
   server components, so changing it requires a redeploy.
4. Deploy, then go back to Render and set `CORS_ORIGINS` to the Vercel URL.

### Verifying the pair is wired up

Open `/data-status` on the deployed site. It shows the backend's health, the
database engine in use and the real row counts. If the API is unreachable the
page says so explicitly, with the URL it tried — it will not quietly render
zeros.

---

## 3. Populating the deployed database

> ### Do not ingest from the Render free tier — measured, not guessed
>
> Triggering ingestion on the deployed service does not work. Two jobs run
> against a healthy Render deployment on 2026-11-25:
>
> | Job | Requests | HTTP 429s | Rows written | Status |
> |---|---|---|---|---|
> | `discover_pools:new` (10 pages) | 48 | **46** | 40 | `partial` |
> | `discover_pools:top` (2 pages) | 10 | **10** | 0 | `failed` |
>
> The identical code, at the same configured 12 req/min, runs cleanly from an
> ordinary machine. GeckoTerminal throttles **per IP**, and Render's free tier
> shares outbound IPs between tenants, so the budget is already spent by other
> customers before your request arrives. Lowering the configured rate does not
> help: the limit is not yours to spend.
>
> **Therefore: the deployed service should SERVE data, not collect it.** Run
> collection from a machine with its own IP and point it at the deployed
> database. That is what the rest of this section does. (A paid Render instance
> with a dedicated IP, or any VPS, would also work.)

The deployed database starts empty. Three ways to fill it:

### a. Run the collector against the deployed database (recommended)

From your own machine — which has its own IP and therefore its own rate-limit
budget — with the **External** connection string from Render (Dashboard →
`solana-lab-db` → "External Database URL"; the *internal* URL only resolves
inside Render's network):

```bash
cd backend
export DATABASE_URL='postgres://…'     # the external URL, not the internal one
export PYTHONPATH=.
./.venv/bin/alembic upgrade head
./.venv/bin/python -m app.cli ingest --feed new --pages 10
./.venv/bin/python -m app.cli ingest --feed top --pages 5
./.venv/bin/python -m app.cli backfill --timeframe hour --limit-pools 50 \
    --min-age-minutes 90 --max-age-days 30 --order oldest --only-missing
./.venv/bin/python -m app.cli build-dataset --label v1 --inactivity-hours 24
```

### b. Schedule it, which is what the research actually needs

The new-pools feed only covers the last ~6 minutes of launches and cannot be
paged back, so the unbiased inception cohort has to be **accumulated**. Run
`scripts/collect.sh` every 5 minutes against the deployed database:

```bash
*/5 * * * * DATABASE_URL='postgres://…' /path/to/scripts/collect.sh >> /var/log/solana-lab.log 2>&1
```

Render Cron Jobs are a paid feature; a cron on any always-on machine, a GitHub
Actions schedule, or a free scheduler all work equally well. Without this the
dataset stays a one-off snapshot and survival analysis remains limited to the
survivorship-biased retrospective sample.

### c. Copy your local dataset up

```bash
cd backend
PYTHONPATH=. ./.venv/bin/python - <<'PY'
# Reads the local SQLite database and writes every row into DATABASE_URL.
# Ordered so foreign keys resolve.
import os
from sqlalchemy import create_engine, select, insert
from app.models import (Base, DataSource, Token, Pool, IngestionJob, RawPayload,
                        MarketObservation, OhlcvCandle, HolderSnapshot,
                        FeatureDefinition, DatasetVersion, PoolFeature,
                        ResearchExperiment, ExperimentResult)
src = create_engine("sqlite:///./solana_lab.db")
dst = create_engine(os.environ["DATABASE_URL"].replace("postgres://", "postgresql+psycopg://", 1))
Base.metadata.create_all(dst)
ORDER = [DataSource, IngestionJob, RawPayload, Token, Pool, MarketObservation,
         OhlcvCandle, HolderSnapshot, FeatureDefinition, DatasetVersion,
         PoolFeature, ResearchExperiment, ExperimentResult]
with src.connect() as s, dst.begin() as d:
    for model in ORDER:
        rows = [dict(r._mapping) for r in s.execute(select(model.__table__))]
        if rows:
            d.execute(insert(model.__table__), rows)
        print(f"{model.__tablename__}: {len(rows)}")
PY
```

---

## 4. Docker Compose (local, full stack)

```bash
cp .env.example .env     # set POSTGRES_PASSWORD
docker compose up --build
docker compose exec backend alembic upgrade head
```

> **Unverified.** The Compose file and both Dockerfiles were written and
> reviewed but never executed: no Docker daemon was available in the
> environment this project was built in. The pieces they depend on *were*
> verified directly — migrations apply, reverse and re-apply against a real
> PostgreSQL 16 server with no drift, and the container entrypoint was run
> end-to-end (migrations applied, health check green). Treat the image build
> itself as untested until you run it.

---

## 5. Cost

Everything above is free:

| Resource | Plan | Limit that matters |
|---|---|---|
| Vercel | Hobby | Fine for this; non-commercial use only |
| Render web service | Free | Sleeps after 15 min idle |
| Render Postgres | Free | **Deleted after 30 days** |
| Neon Postgres | Free | Does not expire — preferred |
| GeckoTerminal / DexScreener | No key | Throttles aggressively; the client backs off |

Nothing in the stack requires a paid plan or an API key.
