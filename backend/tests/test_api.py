"""API tests: status codes, response contracts, empty states, error handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import DiscoveryMethod
from tests.conftest import make_pool_with_history

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def populated_client(client):
    """A client whose database holds pools with real-shaped history."""
    from app.core.db import SessionLocal
    from app.pipeline.features import build_dataset_version
    from app.providers.registry import sync_sources

    session = SessionLocal()
    try:
        sync_sources(session)
        # A mix of dead and live pools so survival analysis has both.
        for i in range(14):
            make_pool_with_history(
                session,
                mint=f"Mint{i:040d}",
                pool_address=f"Pool{i:040d}",
                created_at=NOW - timedelta(hours=200 + i),
                hours=150,
                dies_after_hours=20 + i,
                liquidity=1000.0 * (i + 1),
                volume_per_hour=50.0 * (i + 1),
                dex="pumpswap" if i % 2 else "raydium",
            )
        for i in range(14, 26):
            make_pool_with_history(
                session,
                mint=f"Mint{i:040d}",
                pool_address=f"Pool{i:040d}",
                created_at=NOW - timedelta(hours=60 + i),
                hours=60 + i,
                liquidity=2000.0 * (i + 1),
                volume_per_hour=80.0 * (i + 1),
                dex="orca",
                discovery=DiscoveryMethod.TOP_POOLS if i % 3 == 0 else None,
            )
        build_dataset_version(session, label="test-v1", min_candles=6, now=NOW)
    finally:
        session.close()
    return client


class TestHealth:
    def test_health_reports_database_reachable(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"]["reachable"] is True
        assert "disclaimer" in body

    def test_health_lists_sources_and_holder_flag(self, populated_client):
        body = populated_client.get("/api/health").json()
        names = {s["name"] for s in body["data_sources"]}
        assert {"geckoterminal", "dexscreener", "solana_rpc"} <= names
        # Holder collection must be off by default (public RPC throttles it).
        assert body["holder_collection_enabled"] is False


class TestOverviewEmptyState:
    def test_empty_database_returns_explicit_empty_state(self, client):
        body = client.get("/api/overview").json()
        assert body["dataset_empty"] is True
        # No fabricated totals, and an actionable instruction instead.
        assert body["counts"]["pools"] == 0
        assert "cli" in body["how_to_populate"]

    def test_populated_overview_reports_real_counts(self, populated_client):
        body = populated_client.get("/api/overview").json()
        assert body["dataset_empty"] is False
        assert body["counts"]["pools"] == 26
        assert body["counts"]["ohlcv_candles"] > 0
        assert body["coverage"]["pools_with_history"] > 0
        assert body["cohort_composition"]["inception_cohort_share"] is not None

    def test_overview_surfaces_data_quality_warnings(self, populated_client):
        body = populated_client.get("/api/overview").json()
        codes = {w["code"] for w in body["data_quality_warnings"]}
        # Holder unavailability must always be disclosed.
        assert "holder_data_unavailable" in codes


class TestTokenExplorer:
    def test_lists_with_pagination(self, populated_client):
        body = populated_client.get("/api/tokens?page_size=10").json()
        assert len(body["items"]) == 10
        assert body["pagination"]["total"] == 26
        assert body["pagination"]["total_pages"] == 3
        assert body["pagination"]["has_next"] is True

    def test_second_page_differs(self, populated_client):
        first = populated_client.get("/api/tokens?page=1&page_size=5").json()
        second = populated_client.get("/api/tokens?page=2&page_size=5").json()
        assert {i["pool_address"] for i in first["items"]}.isdisjoint(
            {i["pool_address"] for i in second["items"]}
        )

    def test_search_by_mint(self, populated_client):
        body = populated_client.get("/api/tokens?q=Mint0000").json()
        assert body["pagination"]["total"] >= 1
        assert all("Mint0000" in i["mint"] for i in body["items"])

    def test_search_with_no_match_is_empty_not_error(self, populated_client):
        body = populated_client.get("/api/tokens?q=zzzz-nonexistent").json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0

    def test_cohort_filter(self, populated_client):
        inception = populated_client.get("/api/tokens?cohort=inception").json()
        retro = populated_client.get("/api/tokens?cohort=retrospective").json()
        assert all(
            i["discovery_method"] == "new_pools_feed" for i in inception["items"]
        )
        assert all(i["survivorship_biased"] for i in retro["items"])

    def test_dex_filter(self, populated_client):
        body = populated_client.get("/api/tokens?dex=orca").json()
        assert body["pagination"]["total"] > 0
        assert all(i["dex"] == "orca" for i in body["items"])

    def test_sorting_by_launch_desc(self, populated_client):
        body = populated_client.get("/api/tokens?sort=launch&order=desc").json()
        dates = [i["pool_created_at"] for i in body["items"] if i["pool_created_at"]]
        assert dates == sorted(dates, reverse=True)

    def test_sorting_by_liquidity(self, populated_client):
        body = populated_client.get("/api/tokens?sort=liquidity&order=desc").json()
        assert body["pagination"]["total"] > 0

    def test_has_history_filter(self, populated_client):
        body = populated_client.get("/api/tokens?has_history=true").json()
        assert all(i["data_completeness"]["has_history"] for i in body["items"])

    def test_rejects_invalid_page_size(self, populated_client):
        assert populated_client.get("/api/tokens?page_size=5000").status_code == 422
        assert populated_client.get("/api/tokens?page=0").status_code == 422

    def test_rejects_unknown_sort_key(self, populated_client):
        assert populated_client.get("/api/tokens?sort=nonsense").status_code == 422

    def test_dexes_endpoint(self, populated_client):
        body = populated_client.get("/api/tokens/dexes").json()
        assert body["dexes"]
        assert all("pool_count" in d for d in body["dexes"])


class TestTokenDetail:
    def test_detail_by_pool_address(self, populated_client):
        body = populated_client.get("/api/tokens/Pool0000000000000000000000000000000000000000").json()
        assert body["pool"]["pool_address"].startswith("Pool0000")
        assert body["token"]["mint"].startswith("Mint0000")
        assert body["observation_summary"]["count"] >= 1
        assert "provenance" in body

    def test_detail_by_mint(self, populated_client):
        body = populated_client.get("/api/tokens/Mint0000000000000000000000000000000000000000").json()
        assert body["token"]["mint"].startswith("Mint0000")

    def test_unknown_token_is_404_with_reason(self, populated_client):
        response = populated_client.get("/api/tokens/DoesNotExist")
        assert response.status_code == 404
        assert "not have been ingested" in response.json()["detail"]

    def test_holder_data_reported_unavailable_not_faked(self, populated_client):
        body = populated_client.get("/api/tokens/Pool0000000000000000000000000000000000000000").json()
        assert body["holder_data_available"] is False
        assert body["holder_snapshots"] == []
        assert any("holder" in w.lower() for w in body["missing_data_warnings"])

    def test_history_returns_series(self, populated_client):
        body = populated_client.get(
            "/api/tokens/Pool0000000000000000000000000000000000000000/history"
        ).json()
        assert body["counts"]["candles"] > 0
        assert body["ohlcv"]
        candle = body["ohlcv"][0]
        assert {"ts", "open", "high", "low", "close", "volume_usd"} <= set(candle)

    def test_history_missing_timeframe_says_unavailable(self, populated_client):
        body = populated_client.get(
            "/api/tokens/Pool0000000000000000000000000000000000000000/history?timeframe=day"
        ).json()
        assert body["ohlcv"] == []
        assert any("unavailable, not zero" in n for n in body["notes"])


class TestResearchEndpoints:
    def test_summary_lists_variables_and_warnings(self, populated_client):
        body = populated_client.get("/api/research/summary").json()
        assert body["dataset"]["n_pools"] > 0
        assert body["variables"]
        assert body["feature_availability"]["legal_model_inputs"]
        assert "cause" in body["disclaimer"].lower()

    def test_variables_marks_leakage_legality(self, populated_client):
        body = populated_client.get("/api/research/variables").json()
        by_name = {v["name"]: v for v in body["variables"]}
        assert by_name["initial_liquidity_usd"]["is_legal_model_input"] is True
        assert by_name["duration_hours"]["is_legal_model_input"] is False

    def test_describe_returns_n_and_quantiles(self, populated_client):
        body = populated_client.get(
            "/api/research/describe?column=initial_liquidity_usd"
        ).json()
        assert body["n"] > 0
        assert body["median"] is not None
        assert "missing_share" in body

    def test_describe_unknown_column_is_400(self, populated_client):
        response = populated_client.get("/api/research/describe?column=not_a_column")
        assert response.status_code in (400, 404)

    def test_histogram_has_edges(self, populated_client):
        body = populated_client.get(
            "/api/research/histogram?column=initial_liquidity_usd&bins=8"
        ).json()
        assert len(body["bin_edges"]) == len(body["counts"]) + 1

    def test_correlation_includes_n_ci_and_warnings(self, populated_client):
        body = populated_client.get(
            "/api/research/correlation?x=initial_liquidity_usd&y=duration_hours"
        ).json()
        assert "n" in body and "warnings" in body
        assert body["method"].startswith("Spearman")
        assert any("cause" in w.lower() for w in body["warnings"])

    def test_correlation_matrix_has_adjusted_p_values(self, populated_client):
        body = populated_client.get("/api/research/correlation/matrix").json()
        assert body["columns"]
        for pair in body["pairs"]:
            assert "p_value_adjusted" in pair

    def test_survival_endpoint(self, populated_client):
        body = populated_client.get("/api/research/survival").json()
        assert "n" in body
        if body.get("available"):
            curve = body["curves"][0]
            assert curve["times"] and curve["survival"]
            assert body["events_observed"] + body["censored"] == body["n"]

    def test_survival_stratified(self, populated_client):
        body = populated_client.get("/api/research/survival?group_column=dex").json()
        assert "warnings" in body

    def test_cox_blocks_leaky_covariate(self, populated_client):
        body = populated_client.get(
            "/api/research/survival/cox?covariates=peak_liquidity_usd"
        ).json()
        assert body["available"] is False
        assert body["error"] == "leakage_blocked"
        assert "legal_model_inputs" in body

    def test_compare_groups(self, populated_client):
        body = populated_client.get(
            "/api/research/compare-groups?value_column=duration_hours&group_column=dex"
            "&group_a=orca&group_b=pumpswap"
        ).json()
        assert "n" in body and "warnings" in body

    def test_model_endpoint_rejects_leaky_features(self, populated_client):
        response = populated_client.post(
            "/api/research/model",
            json={"target": "survived_24h", "features": ["total_volume_usd"]},
        )
        body = response.json()
        assert body["available"] is False
        assert body["error"] == "leakage_blocked"

    def test_model_endpoint_runs_or_explains_refusal(self, populated_client):
        body = populated_client.post(
            "/api/research/model", json={"target": "survived_24h"}
        ).json()
        # With a small test dataset it may legitimately refuse; either way it
        # must not invent metrics.
        if body["available"]:
            assert any(m["is_baseline"] for m in body["models"])
            assert body["split"]["strategy"].startswith("time-aware")
        else:
            assert body["message"]

    def test_research_on_empty_database_is_404(self, client):
        response = client.get("/api/research/summary")
        assert response.status_code == 404
        assert "build-dataset" in response.json()["detail"]


class TestDatasets:
    def test_list_and_get(self, populated_client):
        listing = populated_client.get("/api/datasets").json()
        assert listing["count"] >= 1
        version_id = listing["items"][0]["id"]
        detail = populated_client.get(f"/api/datasets/{version_id}").json()
        assert detail["pool_count"] > 0
        assert "criteria" in detail

    def test_unknown_dataset_is_404(self, populated_client):
        assert populated_client.get("/api/datasets/99999").status_code == 404

    def test_duplicate_label_is_409(self, populated_client):
        response = populated_client.post(
            "/api/datasets", json={"label": "test-v1", "min_candles": 6}
        )
        assert response.status_code == 409
        assert "immutable" in response.json()["detail"]

    def test_create_new_version(self, populated_client):
        response = populated_client.post(
            "/api/datasets",
            json={"label": "t48", "inactivity_threshold_hours": 48, "min_candles": 6},
        )
        assert response.status_code == 201
        assert response.json()["inactivity_threshold_hours"] == 48

    def test_rejects_invalid_cohort(self, populated_client):
        response = populated_client.post(
            "/api/datasets", json={"label": "bad", "cohort": "nonsense"}
        )
        assert response.status_code == 422


class TestExperiments:
    def test_create_and_fetch(self, populated_client):
        version_id = populated_client.get("/api/datasets").json()["items"][0]["id"]
        response = populated_client.post(
            "/api/experiments",
            json={
                "name": "Liquidity vs duration",
                "research_question": "Is initial liquidity associated with longer activity?",
                "hypothesis": "Higher initial liquidity is associated with longer activity.",
                "dataset_version_id": version_id,
                "method": "spearman",
                "features_used": ["initial_liquidity_usd"],
                "target_definition": "duration_hours",
                "split_strategy": "none (descriptive)",
                "limitations": "Observational; survivorship-biased subsample.",
                "results": [
                    {"metric_name": "rho", "metric_value": 0.42, "sample_size": 26,
                     "p_value": 0.03, "ci_lower": 0.05, "ci_upper": 0.68},
                ],
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["results"][0]["metric_name"] == "rho"
        assert body["dataset_version"]["id"] == version_id

        fetched = populated_client.get(f"/api/experiments/{body['id']}").json()
        assert fetched["name"] == "Liquidity vs duration"
        assert "reproducibility" in fetched

    def test_rejects_nonexistent_dataset_version(self, populated_client):
        response = populated_client.post(
            "/api/experiments",
            json={
                "name": "bad",
                "research_question": "q",
                "method": "spearman",
                "dataset_version_id": 999999,
            },
        )
        assert response.status_code == 400
        assert "reproducible" in response.json()["detail"]

    def test_requires_research_question(self, populated_client):
        response = populated_client.post(
            "/api/experiments", json={"name": "x", "method": "spearman"}
        )
        assert response.status_code == 422

    def test_unknown_experiment_is_404(self, populated_client):
        assert populated_client.get("/api/experiments/99999").status_code == 404

    def test_comparison_warns_on_mixed_dataset_versions(self, populated_client):
        version_id = populated_client.get("/api/datasets").json()["items"][0]["id"]
        other = populated_client.post(
            "/api/datasets", json={"label": "cmp-v2", "min_candles": 6}
        ).json()["id"]

        first = populated_client.post(
            "/api/experiments",
            json={"name": "a", "research_question": "q", "method": "m",
                  "dataset_version_id": version_id,
                  "results": [{"metric_name": "auc", "metric_value": 0.6}]},
        ).json()
        second = populated_client.post(
            "/api/experiments",
            json={"name": "b", "research_question": "q", "method": "m",
                  "dataset_version_id": other,
                  "results": [{"metric_name": "auc", "metric_value": 0.7}]},
        ).json()

        body = populated_client.get(
            f"/api/experiments/compare/metrics?ids={first['id']}&ids={second['id']}"
        ).json()
        assert len(body["metrics"]) == 1
        assert any("different dataset versions" in w for w in body["warnings"])


class TestIngestionAndQuality:
    def test_jobs_listing(self, populated_client):
        body = populated_client.get("/api/ingestion/jobs").json()
        assert "items" in body

    def test_unknown_job_is_404(self, populated_client):
        assert populated_client.get("/api/ingestion/jobs/99999").status_code == 404

    def test_rate_limits_are_honest_about_unknowns(self, client):
        body = client.get("/api/ingestion/rate-limits").json()
        assert body["documented_limits"] == "unknown"
        assert "429" in body["behaviour_on_429"]

    def test_rejects_page_beyond_new_pools_cap(self, client):
        response = client.post(
            "/api/ingestion/jobs",
            json={"job_type": "discover_pools", "feed": "new", "pages": 15},
        )
        assert response.status_code == 400
        assert "401" in response.json()["detail"]

    def test_data_quality_empty_state(self, client):
        body = client.get("/api/data-quality").json()
        assert body["dataset_empty"] is True

    def test_data_quality_runs_real_checks(self, populated_client):
        body = populated_client.get("/api/data-quality").json()
        assert body["dataset_empty"] is False
        names = {c["name"] for c in body["checks"]}
        assert {
            "observation_field_completeness",
            "duplicate_rows",
            "invalid_values",
            "referential_integrity",
            "holder_concentration_availability",
        } <= names
        checks = {c["name"]: c for c in body["checks"]}
        assert checks["duplicate_rows"]["status"] == "pass"
        assert checks["invalid_values"]["status"] == "pass"
        # Unavailable capability must be reported as not_run, never as passing.
        assert checks["holder_concentration_availability"]["status"] == "not_run"


class TestCorsAndDocs:
    def test_docs_served_outside_production(self, client):
        assert client.get("/docs").status_code == 200
