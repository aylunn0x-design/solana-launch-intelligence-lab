# Project Plan

## Objective

A working research platform for studying the lifecycle of newly launched Solana
tokens, built on real on-chain data, with methodology and limitations stated
explicitly enough that another researcher could check the work.

## Phases and status

| Phase | Scope | Status |
|---|---|---|
| 0 | Environment audit, data-source verification | **Done** — three APIs probed live; blockers found and documented |
| 1 | Repository, config, database layer, CI | **Done** |
| 2 | Schema, migrations, ingestion, validation, job tracking | **Done** — verified on SQLite and PostgreSQL |
| 3 | Data-source integration | **Done** — GeckoTerminal (primary) + DexScreener (cross-validation) live; Solana RPC implemented but blocked |
| 4 | Feature engineering with leakage controls | **Done** |
| 5 | Research engine: descriptive, correlation, survival, models | **Done** |
| 6 | Frontend: 13 routes | **Done** |
| 7 | Integration | **Done** — pages verified rendering live data |
| 8 | QA: tests, lint, empty/error states, security | **Done** — 244 tests |
| 9 | Documentation | **Done** |
| 10 | Portfolio release | **Not started** — needs a longer collection window and a licence |

## Priorities as delivered

**P0 (complete):** foundation, database, one real data source, ingestion, token
records, working backend and frontend, real overview metrics, working explorer,
tests.

**P1 (complete):** historical observations, token detail pages, feature
engineering, correlation analysis, survival analysis, data-quality reporting.

**P2 (partial):** experiment tracking and dataset versioning are built; model
comparison runs through the API. Clustering and PCA are not built.

**P3 (not started):** additional sources, performance work, deployment
optimisation.

## Principal risks, and what happened

| Risk | Outcome |
|---|---|
| No usable free data source | **Avoided.** Two no-key APIs verified live |
| Rate limits block collection | **Materialised.** Handled with an adaptive limiter; collection is slow but reliable |
| No historical depth | **Avoided.** OHLCV returns up to 1000 bars per pool (~41 days hourly) |
| Survivorship bias | **Materialised and unavoidable.** The new-pools feed cannot be paged back, so the unbiased cohort must accumulate over time. Handled by labelling, separating and warning |
| Holder data unavailable | **Materialised.** Public RPC blocks it; RQ2 is out of scope and documented |
| Conclusions are artefacts of definitions | **Materialised and measured.** RQ6 shows the event rate moving 12% → 0% with the threshold alone |

## Definition of done for the MVP

All met except where noted:

- [x] Application starts locally
- [x] Database initialises; migrations apply, reverse and re-apply
- [x] At least one real data source integrated (two are)
- [x] Data can be ingested; validation works
- [x] API endpoints return real data
- [x] Frontend connects to the API
- [x] Overview metrics computed from stored data
- [x] Explorer search, filter, sort and pagination work
- [x] Token detail pages work
- [x] Missing data handled explicitly
- [x] Research calculations reproducible (immutable versions, fixed seeds)
- [x] Statistical methods tested
- [x] Automated tests run (244 passing)
- [ ] **Docker setup works** — written and reviewed, but *not executed*: no
      Docker daemon in the build environment. The PostgreSQL path it depends on
      was verified directly.
- [x] Environment configuration documented
- [x] README usable by another developer
- [x] No fake metrics presented as real
- [x] No secrets committed
- [x] Known limitations documented
