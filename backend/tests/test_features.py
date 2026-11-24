"""Feature engineering, survival definition, and anti-leakage tests.

This is the file that protects the research claims. If `survived_at` or
`compute_survival` is wrong, every downstream statistic is wrong in a way no
amount of UI polish would reveal.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.models import DiscoveryMethod, FeatureAvailability
from app.pipeline.features import (
    FEATURE_BY_NAME,
    FEATURE_REGISTRY,
    LeakageError,
    assert_no_leakage,
    build_dataset_version,
    coefficient_of_variation,
    compute_survival,
    max_drawdown_pct,
    model_input_features,
    outcome_features,
    realized_volatility,
    survived_at,
    sync_feature_definitions,
)
from tests.conftest import make_pool_with_history

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


class TestSurvivedAt:
    """Three-valued survival labels. The censoring trap lives here."""

    def test_reaching_the_horizon_is_true(self):
        assert survived_at(48.0, True, 24) is True
        assert survived_at(24.0, False, 24) is True

    def test_dying_before_the_horizon_is_false(self):
        assert survived_at(10.0, True, 24) is False

    def test_censored_before_the_horizon_is_undetermined(self):
        """A 10-hour-old pool that is still alive has NOT failed to survive 24h.

        Labelling this False would mark every recent launch a failure and make
        any survival rate depend on when collection started.
        """
        assert survived_at(10.0, False, 24) is None

    def test_missing_inputs_give_none(self):
        assert survived_at(None, True, 24) is None
        assert survived_at(10.0, None, 24) is None

    def test_horizon_boundary_is_inclusive(self):
        assert survived_at(24.0, True, 24) is True


class TestComputeSurvival:
    def test_inactive_pool_yields_event_and_duration_to_last_activity(self):
        created = NOW - timedelta(hours=100)
        # Traded for 10 hours, then silent for 90 -> dead.
        candles = [(created + timedelta(hours=h), 50.0) for h in range(10)]
        candles += [(created + timedelta(hours=h), 0.0) for h in range(10, 100)]

        out = compute_survival(created, candles, inactivity_threshold_hours=24, now=NOW)
        assert out.event_observed is True
        assert out.duration_hours == pytest.approx(9.0)
        assert out.active_hours == 10

    def test_recently_active_pool_is_censored(self):
        created = NOW - timedelta(hours=50)
        candles = [(created + timedelta(hours=h), 10.0) for h in range(50)]
        out = compute_survival(created, candles, inactivity_threshold_hours=24, now=NOW)
        assert out.event_observed is False, "still trading -> censored, not dead"
        assert out.duration_hours == pytest.approx(50.0)

    def test_threshold_changes_the_verdict(self):
        """RQ6: the survival definition is a parameter, and it matters."""
        created = NOW - timedelta(hours=60)
        # Last traded 30 hours ago.
        candles = [(created + timedelta(hours=h), 10.0) for h in range(30)]
        strict = compute_survival(created, candles, inactivity_threshold_hours=24, now=NOW)
        lenient = compute_survival(created, candles, inactivity_threshold_hours=48, now=NOW)
        assert strict.event_observed is True
        assert lenient.event_observed is False

    def test_zero_volume_candles_are_not_activity(self):
        created = NOW - timedelta(hours=10)
        candles = [(created + timedelta(hours=h), 0.0) for h in range(10)]
        out = compute_survival(created, candles, inactivity_threshold_hours=24, now=NOW)
        assert out.duration_hours is None
        assert out.active_hours == 0
        assert "no measurable activity" in out.note

    def test_missing_creation_time_is_excluded_not_guessed(self):
        candles = [(NOW - timedelta(hours=h), 10.0) for h in range(5)]
        out = compute_survival(None, candles, inactivity_threshold_hours=24, now=NOW)
        assert out.duration_hours is None
        assert "pool_created_at" in out.note

    def test_activity_before_creation_is_rejected(self):
        """Inconsistent provider timestamps must not produce negative lifetimes."""
        created = NOW - timedelta(hours=10)
        candles = [(created - timedelta(hours=50), 10.0)]
        out = compute_survival(created, candles, inactivity_threshold_hours=1, now=NOW)
        assert out.duration_hours is None
        assert "predates" in out.note

    def test_naive_timestamps_are_treated_as_utc(self):
        """SQLite returns naive datetimes; they must not crash or shift."""
        created = datetime(2026, 11, 24, 0, 0)  # naive
        candles = [(datetime(2026, 11, 24, h, 0), 5.0) for h in range(5)]
        out = compute_survival(created, candles, inactivity_threshold_hours=24, now=NOW)
        assert out.duration_hours is not None


class TestStatisticalHelpers:
    def test_cv_matches_hand_calculation(self):
        # values 2,4,4,4,5,5,7,9 -> mean 5, sample sd = 2.138...
        values = [2, 4, 4, 4, 5, 5, 7, 9]
        expected = math.sqrt(sum((v - 5) ** 2 for v in values) / 7) / 5
        assert coefficient_of_variation(values) == pytest.approx(expected)

    def test_cv_undefined_cases(self):
        assert coefficient_of_variation([5.0]) is None, "needs >= 2 points"
        assert coefficient_of_variation([]) is None
        assert coefficient_of_variation([0.0, 0.0]) is None, "mean zero -> undefined"

    def test_constant_series_has_zero_cv(self):
        assert coefficient_of_variation([7.0, 7.0, 7.0]) == pytest.approx(0.0)

    def test_max_drawdown_simple_case(self):
        # peak 100 -> trough 50 = 50% drawdown
        assert max_drawdown_pct([100.0, 80.0, 50.0, 60.0]) == pytest.approx(50.0)

    def test_monotonic_rise_has_no_drawdown(self):
        assert max_drawdown_pct([1.0, 2.0, 3.0]) == pytest.approx(0.0)

    def test_drawdown_uses_running_peak(self):
        # rises to 200 then falls to 100 -> 50%, not measured from the first point
        assert max_drawdown_pct([100.0, 200.0, 100.0]) == pytest.approx(50.0)

    def test_drawdown_needs_two_points(self):
        assert max_drawdown_pct([100.0]) is None
        assert max_drawdown_pct([]) is None

    def test_realized_volatility_of_constant_price_is_zero(self):
        assert realized_volatility([10.0] * 10) == pytest.approx(0.0)

    def test_realized_volatility_requires_enough_points(self):
        assert realized_volatility([1.0, 2.0]) is None

    def test_realized_volatility_ignores_nonpositive_prices(self):
        # log returns are undefined at 0; those points must be skipped, not crash
        assert realized_volatility([1.0, 0.0, 2.0, 3.0, 4.0]) is not None


class TestLeakageGate:
    def test_registry_declares_availability_for_every_feature(self):
        for spec in FEATURE_REGISTRY:
            assert isinstance(spec.availability, FeatureAvailability)
            assert spec.description.strip(), f"{spec.name} must be documented"

    def test_launch_features_are_accepted(self):
        assert_no_leakage(model_input_features())  # must not raise

    @pytest.mark.parametrize(
        "leaky",
        ["duration_hours", "event_observed", "survived_24h", "peak_liquidity_usd",
         "total_volume_usd", "activity_persistence", "max_drawdown_pct"],
    )
    def test_outcome_and_forward_features_are_blocked(self, leaky):
        with pytest.raises(LeakageError, match="not\\s+known at launch"):
            assert_no_leakage([leaky])

    def test_unknown_feature_is_blocked(self):
        with pytest.raises(LeakageError, match="not in the feature registry"):
            assert_no_leakage(["something_invented"])

    def test_outcomes_are_never_model_inputs(self):
        assert not (set(outcome_features()) & set(model_input_features()))

    def test_error_names_every_offender(self):
        with pytest.raises(LeakageError) as exc:
            assert_no_leakage(["initial_liquidity_usd", "duration_hours", "peak_liquidity_usd"])
        message = str(exc.value)
        assert "duration_hours" in message and "peak_liquidity_usd" in message
        assert "initial_liquidity_usd" not in message, "legal feature must not be blamed"

    def test_initial_liquidity_is_a_launch_feature(self):
        assert FEATURE_BY_NAME["initial_liquidity_usd"].availability is (
            FeatureAvailability.AT_LAUNCH
        )


class TestDatasetBuilding:
    def test_builds_dataset_and_records_criteria(self, seeded_session):
        created = NOW - timedelta(hours=100)
        make_pool_with_history(
            seeded_session,
            mint="Mint1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pool_address="Pool1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            created_at=created,
            hours=40,
        )
        version = build_dataset_version(
            seeded_session, label="t1", inactivity_threshold_hours=24, min_candles=6, now=NOW
        )
        assert version.pool_count == 1
        assert version.inactivity_threshold_hours == 24
        assert version.criteria["min_candles"] == 6
        assert version.inception_cohort_share == pytest.approx(1.0)

    def test_excludes_pools_below_min_candles(self, seeded_session):
        make_pool_with_history(
            seeded_session,
            mint="Mint2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pool_address="Pool2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            created_at=NOW - timedelta(hours=10),
            hours=3,
        )
        version = build_dataset_version(seeded_session, label="t2", min_candles=6, now=NOW)
        assert version.pool_count == 0, "insufficiently observed pools must be excluded"

    def test_labels_are_immutable(self, seeded_session):
        build_dataset_version(seeded_session, label="dup", min_candles=1, now=NOW)
        with pytest.raises(ValueError, match="already exists"):
            build_dataset_version(seeded_session, label="dup", min_candles=1, now=NOW)

    def test_cohort_share_reflects_discovery_mix(self, seeded_session):
        make_pool_with_history(
            seeded_session,
            mint="Mint3aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pool_address="Pool3aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            created_at=NOW - timedelta(hours=100),
            hours=40,
            discovery=DiscoveryMethod.NEW_POOLS_FEED,
        )
        make_pool_with_history(
            seeded_session,
            mint="Mint4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pool_address="Pool4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            created_at=NOW - timedelta(hours=100),
            hours=40,
            discovery=DiscoveryMethod.TOP_POOLS,
        )
        version = build_dataset_version(seeded_session, label="mix", min_candles=6, now=NOW)
        assert version.pool_count == 2
        assert version.inception_cohort_share == pytest.approx(0.5)

    def test_dead_pool_gets_event_and_live_pool_is_censored(self, seeded_session):
        from sqlalchemy import select

        from app.models import PoolFeature

        make_pool_with_history(
            seeded_session,
            mint="MintDeadaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pool_address="PoolDeadaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            created_at=NOW - timedelta(hours=200),
            hours=190,
            dies_after_hours=10,  # silent for the remaining 180h
        )
        make_pool_with_history(
            seeded_session,
            mint="MintLiveaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            pool_address="PoolLiveaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            created_at=NOW - timedelta(hours=50),
            hours=50,  # trading up to NOW
        )
        version = build_dataset_version(
            seeded_session, label="surv", inactivity_threshold_hours=24, min_candles=6, now=NOW
        )
        rows = {
            r.pool.pool_address: r
            for r in seeded_session.execute(
                select(PoolFeature).where(PoolFeature.dataset_version_id == version.id)
            ).scalars()
        }
        dead = rows["PoolDeadaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        live = rows["PoolLiveaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]

        assert dead.event_observed is True
        assert dead.duration_hours == pytest.approx(9.0, abs=1.5)
        assert dead.survived_24h is False
        assert live.event_observed is False
        assert live.survived_24h is True

    def test_feature_definitions_are_synced(self, session):
        added = sync_feature_definitions(session)
        assert added == len(FEATURE_REGISTRY)
        # Idempotent: a second call adds nothing.
        assert sync_feature_definitions(session) == 0
