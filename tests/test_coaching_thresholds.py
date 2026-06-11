"""
Unit tests for server/coaching/thresholds.py

Covers:
  - derive_zones: HR and pace bands computed from LTHR / lt_pace_sec
  - easy_hr_ceiling: returns hr[1] from zones, or fallback
  - fmt_pace: seconds/km → "M:SS/km" string formatting

All tests are fully offline — no network, no Garmin credentials.
"""

import pytest
import coaching.thresholds as thresholds


# ─────────────────────────────────────────────────────────────────────────────
# derive_zones — HR zones
# ─────────────────────────────────────────────────────────────────────────────

class TestDeriveZonesHR:
    def test_all_hr_zones_populated_when_lthr_given(self):
        zones = thresholds.derive_zones(lthr=170, lt_pace_sec=None)
        for zone in ("easy", "aerobic", "threshold", "interval"):
            assert zones[zone]["hr"] is not None
            low, high = zones[zone]["hr"]
            assert low < high, f"{zone} HR zone inverted: {low} >= {high}"

    def test_hr_none_when_lthr_is_none(self):
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=None)
        for zone in ("easy", "aerobic", "threshold", "interval"):
            assert zones[zone]["hr"] is None

    def test_easy_hr_friel_coefficients(self):
        lthr = 170
        zones = thresholds.derive_zones(lthr=lthr, lt_pace_sec=None)
        low, high = zones["easy"]["hr"]
        assert low == round(lthr * 0.70)   # 119
        assert high == round(lthr * 0.84)  # 143

    def test_aerobic_hr_friel_coefficients(self):
        lthr = 170
        zones = thresholds.derive_zones(lthr=lthr, lt_pace_sec=None)
        low, high = zones["aerobic"]["hr"]
        assert low == round(lthr * 0.84)   # 143
        assert high == round(lthr * 0.93)  # 158

    def test_threshold_hr_friel_coefficients(self):
        lthr = 170
        zones = thresholds.derive_zones(lthr=lthr, lt_pace_sec=None)
        low, high = zones["threshold"]["hr"]
        assert low == round(lthr * 0.93)   # 158
        assert high == round(lthr * 1.03)  # 175

    def test_interval_hr_friel_coefficients(self):
        lthr = 170
        zones = thresholds.derive_zones(lthr=lthr, lt_pace_sec=None)
        low, high = zones["interval"]["hr"]
        assert low == round(lthr * 1.03)   # 175
        assert high == round(lthr * 1.12)  # 190

    def test_hr_zones_are_contiguous(self):
        """Adjacent zones should share a boundary (easy.high == aerobic.low, etc.)."""
        zones = thresholds.derive_zones(lthr=165, lt_pace_sec=None)
        assert zones["easy"]["hr"][1] == zones["aerobic"]["hr"][0]
        assert zones["aerobic"]["hr"][1] == zones["threshold"]["hr"][0]
        assert zones["threshold"]["hr"][1] == zones["interval"]["hr"][0]


# ─────────────────────────────────────────────────────────────────────────────
# derive_zones — pace zones
# ─────────────────────────────────────────────────────────────────────────────

class TestDeriveZonesPace:
    def test_all_pace_zones_populated_when_lt_pace_given(self):
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=270)
        for zone in ("easy", "aerobic", "threshold", "interval"):
            assert zones[zone]["pace"] is not None

    def test_pace_none_when_lt_pace_is_none(self):
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=None)
        for zone in ("easy", "aerobic", "threshold", "interval"):
            assert zones[zone]["pace"] is None

    def test_easy_pace_offsets(self):
        lt = 270
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=lt)
        slow, fast = zones["easy"]["pace"]
        assert slow == lt + 45
        assert fast == lt + 90

    def test_aerobic_pace_offsets(self):
        lt = 270
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=lt)
        slow, fast = zones["aerobic"]["pace"]
        assert slow == lt + 20
        assert fast == lt + 45

    def test_threshold_pace_offsets(self):
        lt = 270
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=lt)
        slow, fast = zones["threshold"]["pace"]
        assert slow == lt - 5
        assert fast == lt + 10

    def test_interval_pace_offsets(self):
        lt = 270
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=lt)
        slow, fast = zones["interval"]["pace"]
        assert slow == lt - 30
        assert fast == lt - 5

    def test_pace_zone_convention_slow_then_fast(self):
        """
        In running, slower pace = higher sec/km number.
        derive_zones stores (faster_bound_sec, slower_bound_sec) for easy & aerobic
        and (faster_bound_sec, slower_bound_sec) for threshold/interval.

        Easy zone: (lt+45, lt+90) — lt+45 is faster (lower sec/km), lt+90 is slower.
        """
        lt = 270
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=lt)
        fast_s, slow_s = zones["easy"]["pace"]
        assert fast_s < slow_s, "easy pace[0] should be faster (lower sec/km) than pace[1]"

    def test_both_hr_and_pace_populated_when_both_given(self):
        zones = thresholds.derive_zones(lthr=160, lt_pace_sec=270)
        for zone in ("easy", "aerobic", "threshold", "interval"):
            assert zones[zone]["hr"] is not None
            assert zones[zone]["pace"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# fmt_pace
# ─────────────────────────────────────────────────────────────────────────────

class TestFmtPace:
    def test_none_returns_na(self):
        assert thresholds.fmt_pace(None) == "N/A"

    def test_exact_minutes(self):
        assert thresholds.fmt_pace(300) == "5:00/km"

    def test_with_seconds(self):
        assert thresholds.fmt_pace(330) == "5:30/km"

    def test_seconds_zero_padded(self):
        assert thresholds.fmt_pace(306) == "5:06/km"

    def test_six_minutes(self):
        assert thresholds.fmt_pace(360) == "6:00/km"

    def test_sub_minute_pace(self):
        # 59 sec/km — extreme but should format without crash
        assert thresholds.fmt_pace(59) == "0:59/km"

    def test_float_truncated_to_int(self):
        # The function calls int() so 330.9 → 330 → "5:30/km"
        assert thresholds.fmt_pace(330.9) == "5:30/km"


# ─────────────────────────────────────────────────────────────────────────────
# easy_hr_ceiling
# ─────────────────────────────────────────────────────────────────────────────

class TestEasyHrCeiling:
    def test_returns_upper_bound_from_zones(self):
        zones = thresholds.derive_zones(lthr=170, lt_pace_sec=None)
        expected = round(170 * 0.84)
        assert thresholds.easy_hr_ceiling(zones) == expected

    def test_falls_back_to_default_when_hr_none(self):
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=None)
        assert thresholds.easy_hr_ceiling(zones) == 152  # default fallback

    def test_custom_fallback_value(self):
        zones = thresholds.derive_zones(lthr=None, lt_pace_sec=None)
        assert thresholds.easy_hr_ceiling(zones, fallback=160) == 160

    def test_empty_zones_dict_uses_fallback(self):
        assert thresholds.easy_hr_ceiling({}) == 152

    def test_zones_with_none_hr_entry_uses_fallback(self):
        zones = {"easy": {"hr": None}}
        assert thresholds.easy_hr_ceiling(zones) == 152

    def test_returns_int(self):
        zones = thresholds.derive_zones(lthr=168, lt_pace_sec=None)
        result = thresholds.easy_hr_ceiling(zones)
        assert isinstance(result, int)


# ─────────────────────────────────────────────────────────────────────────────
# get_zones — integration smoke (stubs get_client to avoid network)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetZones:
    def test_get_zones_attaches_meta_keys(self, monkeypatch):
        """get_zones() must always attach _source, _lthr, _lt_pace_sec."""
        def fake_fetch():
            return {"lthr": 165, "lt_pace_sec": 270, "source": "garmin_lt"}

        monkeypatch.setattr(thresholds, "fetch_lactate_threshold", fake_fetch)
        zones = thresholds.get_zones()
        assert "_source" in zones
        assert "_lthr" in zones
        assert "_lt_pace_sec" in zones
        assert zones["_lthr"] == 165
        assert zones["_lt_pace_sec"] == 270

    def test_get_zones_unavailable_source(self, monkeypatch):
        def fake_fetch():
            return {"lthr": None, "lt_pace_sec": None, "source": "unavailable"}

        monkeypatch.setattr(thresholds, "fetch_lactate_threshold", fake_fetch)
        zones = thresholds.get_zones()
        assert zones["_source"] == "unavailable"
        for z in ("easy", "aerobic", "threshold", "interval"):
            assert zones[z]["hr"] is None
            assert zones[z]["pace"] is None
