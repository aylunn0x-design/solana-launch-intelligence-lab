"""Parsing and coercion tests.

These matter more than they look: a missing value silently becoming 0 would
corrupt every downstream statistic, and the two providers disagree on timestamp
units (ISO string vs epoch milliseconds).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.providers.parsing import (
    dig,
    from_epoch,
    strip_network_prefix,
    to_float,
    to_int,
    to_utc_datetime,
)


class TestToFloat:
    def test_parses_high_precision_strings(self):
        # GeckoTerminal returns prices with 60+ significant digits.
        raw = "0.000000081888618242236932301967866371665280405519695668972050182104"
        assert to_float(raw) == pytest.approx(8.1888618242e-8, rel=1e-6)

    @pytest.mark.parametrize("value", [None, "", "  ", "none", "None", "null", "NaN", "-", "abc", {}, []])
    def test_unparseable_becomes_none_not_zero(self, value):
        # The critical guarantee: missing must never become 0.
        assert to_float(value) is None

    def test_rejects_nan_and_inf(self):
        assert to_float(float("nan")) is None
        assert to_float(float("inf")) is None
        assert to_float("inf") is None

    def test_booleans_are_not_numbers(self):
        # bool is a subclass of int; treating True as 1.0 would be a silent lie.
        assert to_float(True) is None
        assert to_float(False) is None

    def test_accepts_plain_numbers(self):
        assert to_float(5) == 5.0
        assert to_float(-2.5) == -2.5
        assert to_float("1e3") == 1000.0


class TestToInt:
    def test_truncates_float_strings(self):
        assert to_int("42.9") == 42

    def test_missing_is_none(self):
        assert to_int(None) is None
        assert to_int("abc") is None

    def test_booleans_rejected(self):
        assert to_int(True) is None


class TestTimestamps:
    def test_iso_with_z_is_utc(self):
        dt = to_utc_datetime("2026-11-24T22:25:01Z")
        assert dt == datetime(2026, 11, 24, 22, 25, 1, tzinfo=timezone.utc)
        assert dt.tzinfo is not None

    def test_dexscreener_epoch_milliseconds(self):
        # 1688106058000 ms = 2023-06-30T07:40:58Z
        dt = to_utc_datetime(1688106058000)
        assert dt.year == 2023 and dt.month == 6 and dt.day == 30
        assert dt.tzinfo == timezone.utc

    def test_epoch_seconds_also_supported(self):
        dt = to_utc_datetime(1688106058)
        assert dt.year == 2023 and dt.month == 6

    def test_ms_and_seconds_agree(self):
        """The ms/seconds heuristic must not shift the instant."""
        assert from_epoch(1688106058) == from_epoch(1688106058000)

    def test_naive_datetime_is_assumed_utc(self):
        dt = to_utc_datetime(datetime(2026, 1, 1, 0, 0))
        assert dt.tzinfo == timezone.utc

    def test_invalid_timestamps_are_none(self):
        assert to_utc_datetime("not a date") is None
        assert to_utc_datetime(None) is None
        assert to_utc_datetime(0) is None
        assert to_utc_datetime(-5) is None


class TestHelpers:
    def test_strips_network_prefix(self):
        assert (
            strip_network_prefix("solana_8zknH3bQTduTBKMU61ZsFMVwQLtVqPsS5pBzbzZgpump")
            == "8zknH3bQTduTBKMU61ZsFMVwQLtVqPsS5pBzbzZgpump"
        )

    def test_leaves_unprefixed_alone(self):
        assert strip_network_prefix("abc123") == "abc123"

    def test_dig_survives_missing_levels(self):
        payload = {"relationships": {"base_token": {"data": {"id": "solana_x"}}}}
        assert dig(payload, "relationships", "base_token", "data", "id") == "solana_x"
        assert dig(payload, "relationships", "missing", "data", "id") is None
        assert dig(payload, "relationships", "base_token", "data", "id", "too_deep") is None
        assert dig(None, "a") is None
        assert dig("a string", "a") is None

    def test_dig_default_applies_to_null_values(self):
        assert dig({"a": None}, "a", default="fallback") == "fallback"


def test_no_silent_nan_reaches_storage():
    """Sanity: nothing in the coercion layer can emit NaN."""
    for value in [float("nan"), "nan", float("inf"), "-inf"]:
        result = to_float(value)
        assert result is None or not math.isnan(result)
