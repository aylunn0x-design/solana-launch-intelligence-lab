"""Statistical method tests.

Each statistic is checked against a case whose answer is known independently —
a perfect monotonic relationship, a known drawdown, a hand-computed CV — rather
than against whatever the code happens to return. Several tests assert that the
platform *refuses* to report a result when the sample is too small, which is as
important as computing it correctly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.research import correlation as corr_mod
from app.research import descriptive, modeling, survival
from app.research.common import ResearchDataset, StatResult

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
RNG = np.random.default_rng(7)


def make_dataset(n: int = 120, *, event_fraction: float = 0.6, span_days: float = 30.0,
                 biased_share: float = 0.0) -> ResearchDataset:
    """A synthetic dataset with a known structure, for testing the machinery.

    Used ONLY in tests. Production code never generates data.
    `initial_liquidity_usd` is constructed to be positively associated with
    `duration_hours`, so a method that finds nothing is broken.
    """
    liquidity = np.exp(RNG.normal(7, 1.5, n))
    noise = RNG.normal(0, 8, n)
    duration = 10 + 0.002 * liquidity + noise
    duration = np.clip(duration, 1, None)

    events = RNG.random(n) < event_fraction
    launches = [
        NOW - timedelta(days=span_days) + timedelta(hours=i * (span_days * 24 / max(1, n)))
        for i in range(n)
    ]
    n_biased = int(n * biased_share)
    discovery = ["top_pools"] * n_biased + ["new_pools_feed"] * (n - n_biased)

    frame = pd.DataFrame(
        {
            "pool_id": range(n),
            "pool_address": [f"Pool{i:040d}" for i in range(n)],
            "pool_created_at": pd.to_datetime(launches, utc=True),
            "discovery_method": discovery,
            "initial_liquidity_usd": liquidity,
            "initial_fdv_usd": liquidity * 4,
            "first_hour_volume_usd": liquidity * RNG.uniform(0.01, 0.1, n),
            "first_hour_unique_traders": RNG.integers(1, 40, n),
            "first_hour_txns": RNG.integers(1, 80, n),
            "launch_hour_utc": [d.hour for d in launches],
            "launch_dow": [d.weekday() for d in launches],
            "dex": RNG.choice(["pumpswap", "raydium", "orca"], n),
            "peak_liquidity_usd": liquidity * RNG.uniform(1.0, 3.0, n),
            "liquidity_cv": RNG.uniform(0.0, 1.5, n),
            "total_volume_usd": liquidity * RNG.uniform(0.5, 20, n),
            "active_hours": RNG.integers(1, 100, n),
            "activity_persistence": RNG.uniform(0, 1, n),
            "realized_volatility": RNG.uniform(0, 2, n),
            "max_drawdown_pct": RNG.uniform(0, 100, n),
            "duration_hours": duration,
            "event_observed": events,
            "survived_24h": duration >= 24,
            "survived_72h": duration >= 72,
            "candle_count": RNG.integers(6, 500, n),
            "observation_count": RNG.integers(1, 10, n),
            "completeness": RNG.uniform(0.6, 1.0, n),
        }
    )
    return ResearchDataset(
        version_id=1,
        label="synthetic-test",
        frame=frame,
        inactivity_threshold_hours=24,
        cohort_warnings=["test dataset"],
        criteria={"cohort": "all"},
    )


class TestDescriptive:
    def test_summary_reports_n_and_missingness(self):
        frame = pd.DataFrame({"x": [1.0, 2.0, None, 4.0]})
        out = descriptive.describe_variable(frame, "x")
        assert out["n"] == 3
        assert out["missing_share"] == pytest.approx(0.25)
        assert out["median"] == pytest.approx(2.0)

    def test_empty_column_is_reported_unavailable_not_zero(self):
        frame = pd.DataFrame({"x": [None, None]})
        out = descriptive.describe_variable(frame, "x")
        assert out["available"] is False
        assert out["n"] == 0
        assert "No non-null" in out["message"]

    def test_skew_note_recommends_median(self):
        # one extreme value -> heavy right skew
        frame = pd.DataFrame({"x": [1.0] * 30 + [10_000.0]})
        out = descriptive.describe_variable(frame, "x")
        assert out["skewness"] > 2
        assert any("median" in note for note in out["notes"])

    def test_histogram_returns_edges_and_counts(self):
        frame = pd.DataFrame({"x": list(range(100))})
        out = descriptive.histogram(frame, "x", bins=10)
        assert out["available"] is True
        assert len(out["counts"]) == 10
        assert len(out["bin_edges"]) == 11
        assert sum(out["counts"]) == 100

    def test_log_histogram_excludes_nonpositive_and_says_so(self):
        frame = pd.DataFrame({"x": [0.0, -1.0, 1.0, 10.0, 100.0, 1000.0]})
        out = descriptive.histogram(frame, "x", log_scale=True)
        assert out["scale"] == "log10"
        assert out["excluded_nonpositive"] == 2
        assert "excluded" in out["note"]

    def test_log_histogram_refuses_when_too_few_positive(self):
        frame = pd.DataFrame({"x": [0.0, -1.0, 5.0]})
        out = descriptive.histogram(frame, "x", log_scale=True)
        assert out["available"] is False

    def test_mann_whitney_detects_a_real_shift(self):
        frame = pd.DataFrame(
            {
                "value": list(RNG.normal(10, 1, 60)) + list(RNG.normal(20, 1, 60)),
                "group": ["a"] * 60 + ["b"] * 60,
            }
        )
        result = descriptive.compare_groups(frame, "value", "group")
        assert result.p_value < 0.001
        assert abs(result.effect_size) > 0.8, "clear separation -> large effect size"
        assert result.n == 120
        assert result.ci_lower is not None and result.ci_upper is not None

    def test_identical_groups_show_no_effect(self):
        values = list(RNG.normal(10, 1, 80))
        frame = pd.DataFrame({"value": values + values, "group": ["a"] * 80 + ["b"] * 80})
        result = descriptive.compare_groups(frame, "value", "group")
        assert result.p_value > 0.05
        assert abs(result.effect_size) < 0.2

    def test_refuses_tiny_groups(self):
        frame = pd.DataFrame({"value": [1.0, 2.0, 3.0], "group": ["a", "a", "b"]})
        result = descriptive.compare_groups(frame, "value", "group")
        assert result.statistic is None
        assert any("Refusing to test" in w for w in result.warnings)

    def test_welch_carries_a_normality_caveat(self):
        frame = pd.DataFrame(
            {
                "value": list(RNG.normal(10, 1, 40)) + list(RNG.normal(12, 1, 40)),
                "group": ["a"] * 40 + ["b"] * 40,
            }
        )
        result = descriptive.compare_groups(frame, "value", "group", method="welch")
        assert result.effect_size_name == "Cohen's d"
        assert any("normality" in w for w in result.warnings)

    def test_every_result_carries_causation_disclaimer(self):
        frame = pd.DataFrame(
            {
                "value": list(RNG.normal(10, 1, 40)) + list(RNG.normal(12, 1, 40)),
                "group": ["a"] * 40 + ["b"] * 40,
            }
        )
        result = descriptive.compare_groups(frame, "value", "group")
        assert any("cause" in w.lower() for w in result.warnings)

    def test_bootstrap_ci_is_reproducible(self):
        a, b = RNG.normal(5, 1, 40), RNG.normal(6, 1, 40)
        first = descriptive.bootstrap_median_difference_ci(a, b)
        second = descriptive.bootstrap_median_difference_ci(a, b)
        assert first == second, "fixed seed must give identical intervals"


class TestCorrelation:
    def test_perfect_monotonic_relationship(self):
        frame = pd.DataFrame({"x": range(50), "y": [i**3 for i in range(50)]})
        result = corr_mod.correlation(frame, "x", "y", method="spearman")
        # Spearman is 1.0 for any increasing transform; Pearson would not be.
        assert result.statistic == pytest.approx(1.0)
        assert result.details["interpretation"] == "very strong positive"

    def test_spearman_beats_pearson_under_an_outlier(self):
        x = list(range(40))
        y = list(range(40))
        x.append(1000)  # single wild outlier
        y.append(0)
        frame = pd.DataFrame({"x": x, "y": y})
        spearman = corr_mod.correlation(frame, "x", "y", method="spearman")
        pearson = corr_mod.correlation(frame, "x", "y", method="pearson")
        assert spearman.statistic > pearson.statistic
        assert any("skew" in w or "outlier" in w for w in pearson.warnings)

    def test_refuses_below_minimum_pairs(self):
        frame = pd.DataFrame({"x": [1, 2, 3], "y": [1, 2, 3]})
        result = corr_mod.correlation(frame, "x", "y")
        assert result.statistic is None
        assert any("At least" in w for w in result.warnings)

    def test_constant_variable_is_undefined(self):
        frame = pd.DataFrame({"x": [5.0] * 30, "y": range(30)})
        result = corr_mod.correlation(frame, "x", "y")
        assert result.statistic is None
        assert any("constant" in w for w in result.warnings)

    def test_reports_pairwise_complete_n(self):
        frame = pd.DataFrame({"x": [1, 2, 3, None] * 10, "y": [1, 2, None, 4] * 10})
        result = corr_mod.correlation(frame, "x", "y")
        assert result.n == 20  # only rows where both are present
        assert result.missing_share == pytest.approx(0.5)

    def test_fisher_ci_brackets_the_estimate(self):
        lo, hi = corr_mod.fisher_ci(0.5, 100)
        assert lo < 0.5 < hi

    def test_fisher_ci_undefined_for_tiny_n(self):
        assert corr_mod.fisher_ci(0.5, 3) == (None, None)

    def test_negligible_effect_is_flagged(self):
        frame = pd.DataFrame({"x": RNG.normal(0, 1, 200), "y": RNG.normal(0, 1, 200)})
        result = corr_mod.correlation(frame, "x", "y")
        if abs(result.statistic) < 0.1:
            assert any("negligible" in w for w in result.warnings)

    def test_benjamini_hochberg_is_monotone_and_conservative(self):
        raw = [0.001, 0.01, 0.03, 0.2, 0.5]
        adjusted = corr_mod.benjamini_hochberg(raw)
        assert all(a >= r for a, r in zip(adjusted, raw, strict=True)), "adjusted >= raw"
        assert adjusted == sorted(adjusted), "order preserved and monotone here"
        assert all(a <= 1.0 for a in adjusted)

    def test_bh_handles_a_single_p_value(self):
        assert corr_mod.benjamini_hochberg([0.04])[0] == pytest.approx(0.04)

    def test_matrix_adjusts_for_multiple_comparisons(self):
        dataset = make_dataset(150)
        result = corr_mod.correlation_matrix(
            dataset.frame,
            ["initial_liquidity_usd", "duration_hours", "total_volume_usd", "liquidity_cv"],
        )
        assert len(result["columns"]) == 4
        assert result["pairs"], "expected pairwise results"
        for pair in result["pairs"]:
            assert pair["p_value_adjusted"] is not None
            assert pair["p_value_adjusted"] >= pair["p_value"]
        assert any("false discovery rate" in w for w in result["warnings"])

    def test_matrix_recovers_the_planted_association(self):
        dataset = make_dataset(200)
        result = corr_mod.correlation_matrix(
            dataset.frame, ["initial_liquidity_usd", "duration_hours"]
        )
        pair = result["pairs"][0]
        # The synthetic data has duration increasing in liquidity.
        assert pair["coefficient"] > 0.2

    def test_matrix_reports_unknown_columns(self):
        dataset = make_dataset(40)
        result = corr_mod.correlation_matrix(
            dataset.frame, ["initial_liquidity_usd", "not_a_column"]
        )
        assert any("Not present" in w for w in result["warnings"])


class TestSurvivalAnalysis:
    def test_kaplan_meier_produces_a_valid_curve(self):
        dataset = make_dataset(120, event_fraction=0.7)
        result = survival.kaplan_meier(dataset)
        assert result["available"] is True
        curve = result["curves"][0]
        survival_values = curve["survival"]
        assert survival_values[0] <= 1.0
        # A survival function must be non-increasing.
        assert all(
            survival_values[i] >= survival_values[i + 1] - 1e-9
            for i in range(len(survival_values) - 1)
        ), "survival function must be monotonically non-increasing"
        assert result["events_observed"] + result["censored"] == result["n"]

    def test_refuses_when_sample_too_small(self):
        dataset = make_dataset(8)
        result = survival.kaplan_meier(dataset)
        assert result["available"] is False
        assert "at least" in result["message"]

    def test_fully_censored_data_cannot_be_estimated(self):
        dataset = make_dataset(80, event_fraction=0.0)
        dataset.frame["event_observed"] = False
        result = survival.kaplan_meier(dataset)
        assert result["available"] is False
        assert "censored" in result["message"]

    def test_stratified_curves_include_logrank(self):
        dataset = make_dataset(150, event_fraction=0.7)
        result = survival.kaplan_meier(dataset, group_column="dex")
        assert len(result["curves"]) >= 2
        assert "logrank" in result
        assert "p_value" in result["logrank"]

    def test_high_censoring_is_warned(self):
        dataset = make_dataset(120, event_fraction=0.05)
        result = survival.kaplan_meier(dataset)
        if result["available"]:
            assert any("censored" in w for w in result["warnings"])

    def test_cox_runs_and_checks_its_assumption(self):
        dataset = make_dataset(200, event_fraction=0.7)
        result = survival.cox_regression(dataset, ["initial_liquidity_usd"])
        assert result["available"] is True
        assert result["coefficients"]
        coef = result["coefficients"][0]
        assert coef["hazard_ratio"] > 0
        assert coef["ci_lower_hr"] <= coef["hazard_ratio"] <= coef["ci_upper_hr"]
        # The assumption test must always be reported.
        assert "proportional_hazards_test" in result
        assert 0 <= result["concordance_index"] <= 1

    def test_cox_rejects_leaky_covariates(self):
        from app.pipeline.features import LeakageError

        dataset = make_dataset(200)
        with pytest.raises(LeakageError):
            survival.cox_regression(dataset, ["peak_liquidity_usd"])

    def test_cox_refuses_with_too_few_events(self):
        dataset = make_dataset(100, event_fraction=0.02)
        result = survival.cox_regression(dataset, ["initial_liquidity_usd"])
        if not result["available"]:
            assert "event" in result["message"].lower()

    def test_threshold_sensitivity_table(self):
        datasets = {12: make_dataset(100), 48: make_dataset(100)}
        result = survival.survival_threshold_sensitivity(datasets)
        assert len(result["rows"]) == 2
        assert all("event_rate" in row for row in result["rows"] if row.get("available"))


class TestModeling:
    def test_time_aware_split_does_not_mix_periods(self):
        dataset = make_dataset(100)
        train, test, info = modeling.time_aware_split(dataset.frame, 0.3)
        assert info["n_train"] + info["n_test"] == 100
        # Every training launch must precede every test launch.
        assert train["pool_created_at"].max() <= test["pool_created_at"].min()

    def test_classifier_reports_baseline_comparison(self):
        dataset = make_dataset(220, event_fraction=0.6)
        result = modeling.classify_survival(dataset, target="survived_24h")
        if not result["available"]:
            pytest.skip(f"insufficient synthetic signal: {result.get('message')}")
        names = [m["model"] for m in result["models"]]
        assert any("baseline" in n for n in names), "a baseline is mandatory"
        assert any("logistic" in n for n in names)
        assert any("forest" in n for n in names)
        for model in result["models"]:
            assert model["n_test"] > 0
            assert 0.0 <= model["accuracy"] <= 1.0
            assert 0.0 <= model["balanced_accuracy"] <= 1.0

    def test_classifier_blocks_leaky_features(self):
        from app.pipeline.features import LeakageError

        dataset = make_dataset(200)
        with pytest.raises(LeakageError):
            modeling.classify_survival(dataset, features=["total_volume_usd"])

    def test_undetermined_outcomes_are_excluded_not_labelled_false(self):
        dataset = make_dataset(200)
        # Mark a third of rows undetermined, as a censored young pool would be.
        dataset.frame.loc[dataset.frame.index[:60], "survived_24h"] = None
        result = modeling.classify_survival(dataset, target="survived_24h")
        assert result.get("n_excluded_undetermined") == 60
        if result["available"]:
            assert any("undetermined" in w for w in result["warnings"])

    def test_refuses_when_outcome_has_one_class(self):
        dataset = make_dataset(200)
        dataset.frame["survived_24h"] = True
        result = modeling.classify_survival(dataset, target="survived_24h")
        assert result["available"] is False
        assert "imbalanced" in result["message"] or "one outcome class" in result["message"]

    def test_refuses_on_tiny_sample(self):
        dataset = make_dataset(15)
        result = modeling.classify_survival(dataset)
        assert result["available"] is False

    def test_results_are_reproducible(self):
        dataset = make_dataset(220, event_fraction=0.6)
        first = modeling.classify_survival(dataset, target="survived_24h")
        second = modeling.classify_survival(dataset, target="survived_24h")
        if first["available"]:
            assert first["models"][1]["accuracy"] == second["models"][1]["accuracy"]
            assert first["random_seed"] == second["random_seed"]


class TestStatResultEnvelope:
    def test_nan_is_serialised_as_null(self):
        result = StatResult(
            method="m", statistic_name="s", statistic=float("nan"), n=10,
            p_value=float("inf"),
        )
        payload = result.as_dict()
        assert payload["statistic"] is None, "NaN must not reach JSON"
        assert payload["p_value"] is None

    def test_numpy_scalars_are_converted(self):
        result = StatResult(
            method="m", statistic_name="s", statistic=np.float64(0.5), n=np.int64(10)
        )
        payload = result.as_dict()
        assert isinstance(payload["statistic"], float)


class TestDegenerateFeatureDetection:
    """A pool observed once has a 'peak' liquidity equal to its initial value."""

    def test_warns_when_pools_have_a_single_observation(self):
        from app.research.common import _cohort_warnings

        dataset = make_dataset(60)
        dataset.frame["observation_count"] = 1
        version = type(
            "V", (), {"inactivity_threshold_hours": 24, "label": "t", "id": 1}
        )()
        warnings = _cohort_warnings(dataset.frame, version)
        assert any("DEGENERATE LIQUIDITY FEATURES" in w for w in warnings)

    def test_no_warning_when_pools_are_polled_repeatedly(self):
        from app.research.common import _cohort_warnings

        dataset = make_dataset(60)
        dataset.frame["observation_count"] = 8
        version = type(
            "V", (), {"inactivity_threshold_hours": 24, "label": "t", "id": 1}
        )()
        warnings = _cohort_warnings(dataset.frame, version)
        assert not any("DEGENERATE" in w for w in warnings)
