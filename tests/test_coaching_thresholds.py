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
        """get_zones() must always attach _lt_source, _lthr, _lt_pace_sec."""
        def fake_fetch():
            return {"lthr": 165, "lt_pace_sec": 270,
                    "measured_on": "2026-01-01", "source": "garmin_lt"}

        monkeypatch.setattr(thresholds, "fetch_lactate_threshold", fake_fetch)
        # Stub the resolver: it reads the user's real calibration file otherwise.
        monkeypatch.setattr(thresholds, "resolve_lt_pace", lambda lthr, pace: (pace, "garmin_lt"))
        zones = thresholds.get_zones()
        assert "_lt_source" in zones
        assert "_lthr" in zones
        assert "_lt_pace_sec" in zones
        assert zones["_lthr"] == 165
        assert zones["_lt_pace_sec"] == 270

    def test_get_zones_uses_resolved_pace_not_garmins(self, monkeypatch):
        """The resolved pace builds the zones; Garmin's raw value is kept only for display."""
        def fake_fetch():
            return {"lthr": 183, "lt_pace_sec": 356,
                    "measured_on": "2026-03-16", "source": "garmin_lt"}

        monkeypatch.setattr(thresholds, "fetch_lactate_threshold", fake_fetch)
        monkeypatch.setattr(thresholds, "resolve_lt_pace", lambda lthr, pace: (293, "laps"))
        zones = thresholds.get_zones()

        assert zones["_lt_pace_sec"] == 293
        assert zones["_garmin_pace_sec"] == 356
        assert zones["_pace_source"] == "laps"
        assert zones["_measured_on"] == "2026-03-16"
        # zones must be built off 293, not 356
        assert zones["threshold"]["pace"] == (288, 303)

    def test_get_zones_unavailable_source(self, monkeypatch):
        def fake_fetch():
            return {"lthr": None, "lt_pace_sec": None, "source": "unavailable"}

        monkeypatch.setattr(thresholds, "fetch_lactate_threshold", fake_fetch)
        zones = thresholds.get_zones()
        assert zones["_lt_source"] == "unavailable"
        for z in ("easy", "aerobic", "threshold", "interval"):
            assert zones[z]["hr"] is None
            assert zones[z]["pace"] is None


# ─────────────────────────────────────────────────────────────────────────────
# median_threshold_pace — lap-derived LT anchor
# ─────────────────────────────────────────────────────────────────────────────

LTHR = 183          # band = 170.2 – 188.5 bpm

# Real laps from the 2026-07-07 / 07-20 / 07-26 tempo sessions.
REAL_LAPS = [
    (1000, 291.5, 179), (1000, 293.3, 181), (1000, 296.0, 171),   # 07-07  4:51–4:56
    (1000, 287.5, 173), (1000, 300.7, 174), (1000, 302.1, 171),   # 07-20  4:47–5:02
    (1000, 314.4, 175), (1000, 322.4, 173),                       # 07-26  5:14–5:22
]


class TestMedianThresholdPace:
    def test_real_laps_land_near_five_min(self):
        pace = thresholds.median_threshold_pace(REAL_LAPS, LTHR)
        assert pace is not None
        assert 285 <= pace <= 305, f"expected ~4:50–5:05/km, got {pace}s"

    def test_rejects_short_recovery_laps(self):
        """HR lags effort, so a 200m jog after a rep carries the rep's HR at jog pace.
        Left in, it drags the median minutes slow."""
        noisy = REAL_LAPS + [(200, 700.8, 172), (200, 1133.8, 175)]
        assert (thresholds.median_threshold_pace(noisy, LTHR)
                == thresholds.median_threshold_pace(REAL_LAPS, LTHR))

    def test_rejects_short_vo2_reps(self):
        """800m reps at 3:53–4:16/km sit inside the threshold HR band but are VO2 work.
        Left in, they drag the anchor fast and every prescribed tempo pace with it."""
        noisy = REAL_LAPS + [(800, 233.6, 182), (800, 256.1, 178), (800, 275.8, 177)]
        assert (thresholds.median_threshold_pace(noisy, LTHR)
                == thresholds.median_threshold_pace(REAL_LAPS, LTHR))

    def test_excludes_out_of_band_hr(self):
        noisy = REAL_LAPS + [(1000, 380.0, 145), (1000, 370.0, 152), (1000, 240.0, 195)]
        assert (thresholds.median_threshold_pace(noisy, LTHR)
                == thresholds.median_threshold_pace(REAL_LAPS, LTHR))

    def test_returns_none_below_min_samples(self):
        assert thresholds.median_threshold_pace(REAL_LAPS[:4], LTHR) is None

    def test_returns_none_without_lthr(self):
        assert thresholds.median_threshold_pace(REAL_LAPS, None) is None

    def test_median_resists_one_bad_lap(self):
        """One mis-tagged lap must not move the anchor — this is why it's a median."""
        baseline = thresholds.median_threshold_pace(REAL_LAPS, LTHR)
        skewed = thresholds.median_threshold_pace(REAL_LAPS + [(1000, 200.0, 180)], LTHR)
        assert baseline is not None and skewed is not None
        assert abs(skewed - baseline) <= 5


class TestResolveLtPace:
    def test_manual_override_wins(self, monkeypatch):
        monkeypatch.setattr(thresholds, "read_state", lambda: {"lt_pace_sec": 295})
        assert thresholds.resolve_lt_pace(183, 356) == (295, "manual")

    def test_fresh_cache_used_without_refetch(self, monkeypatch):
        from datetime import date
        monkeypatch.setattr(thresholds, "read_state", lambda: {
            "lt_pace_derived": 293, "lt_pace_derived_on": date.today().isoformat()})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps",
                            lambda *a, **k: pytest.fail("should not refetch on fresh cache"))
        assert thresholds.resolve_lt_pace(183, 356) == (293, "laps")

    def test_stale_cache_triggers_rederive(self, monkeypatch):
        monkeypatch.setattr(thresholds, "read_state", lambda: {
            "lt_pace_derived": 999, "lt_pace_derived_on": "2020-01-01"})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: REAL_LAPS)
        monkeypatch.setattr(thresholds, "update_state", lambda **kw: None)
        pace, source = thresholds.resolve_lt_pace(183, 356)
        assert source == "laps" and pace != 999

    def test_falls_back_to_garmin_when_laps_insufficient(self, monkeypatch):
        monkeypatch.setattr(thresholds, "read_state", lambda: {})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: REAL_LAPS[:2])
        assert thresholds.resolve_lt_pace(183, 356) == (356, "garmin_lt")

    def test_falls_back_to_garmin_when_fetch_raises(self, monkeypatch):
        """A Garmin outage must degrade to the stored value, not blow up get_zones."""
        def boom(*a, **k):
            raise RuntimeError("garmin down")
        monkeypatch.setattr(thresholds, "read_state", lambda: {})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", boom)
        assert thresholds.resolve_lt_pace(183, 356) == (356, "garmin_lt")


class TestParsePace:
    """The calibration file is hand-edited, so a typo must degrade, never raise."""

    def test_accepts_seconds_int(self):
        assert thresholds.parse_pace(295) == 295

    def test_accepts_m_ss_string(self):
        assert thresholds.parse_pace("4:55") == 295

    def test_accepts_numeric_string(self):
        assert thresholds.parse_pace("295") == 295

    @pytest.mark.parametrize("bad", ["", "abc", "4:xx", None, True, -10, 0, "  "])
    def test_returns_none_never_raises(self, bad):
        assert thresholds.parse_pace(bad) is None

    def test_malformed_override_does_not_break_resolution(self, monkeypatch):
        """Regression: int("4:55") used to raise straight out of get_zones."""
        monkeypatch.setattr(thresholds, "read_state", lambda: {"lt_pace_sec": "nonsense"})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: [])
        monkeypatch.setattr(thresholds, "update_state", lambda **kw: None)
        assert thresholds.resolve_lt_pace(183, 356) == (356, "garmin_lt")


class TestFailedDerivationCaching:
    def test_failure_is_cached(self, monkeypatch):
        """Regression: a thin training block re-ran the full activity scan every call."""
        from datetime import date
        written = {}
        monkeypatch.setattr(thresholds, "read_state", lambda: {})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: [])
        monkeypatch.setattr(thresholds, "update_state", lambda **kw: written.update(kw))

        assert thresholds.resolve_lt_pace(183, 356) == (356, "garmin_lt")
        assert written["lt_pace_derive_failed_on"] == date.today().isoformat()

    def test_recent_failure_skips_refetch(self, monkeypatch):
        from datetime import date
        monkeypatch.setattr(thresholds, "read_state", lambda: {
            "lt_pace_derive_failed_on": date.today().isoformat()})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps",
                            lambda *a, **k: pytest.fail("should not refetch after recent failure"))
        assert thresholds.resolve_lt_pace(183, 356) == (356, "garmin_lt")

    def test_old_failure_allows_retry(self, monkeypatch):
        monkeypatch.setattr(thresholds, "read_state", lambda: {
            "lt_pace_derive_failed_on": "2020-01-01"})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: REAL_LAPS)
        monkeypatch.setattr(thresholds, "update_state", lambda **kw: None)
        pace, source = thresholds.resolve_lt_pace(183, 356)
        assert source == "laps"

    def test_success_clears_failure_marker(self, monkeypatch):
        written = {}
        monkeypatch.setattr(thresholds, "read_state", lambda: {
            "lt_pace_derive_failed_on": "2020-01-01"})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: REAL_LAPS)
        monkeypatch.setattr(thresholds, "update_state", lambda **kw: written.update(kw))
        thresholds.resolve_lt_pace(183, 356)
        assert written["lt_pace_derive_failed_on"] is None

    def test_corrupt_stamp_treated_as_expired(self, monkeypatch):
        monkeypatch.setattr(thresholds, "read_state", lambda: {
            "lt_pace_derived": 293, "lt_pace_derived_on": "not-a-date"})
        monkeypatch.setattr(thresholds, "fetch_threshold_laps", lambda *a, **k: REAL_LAPS)
        monkeypatch.setattr(thresholds, "update_state", lambda **kw: None)
        pace, source = thresholds.resolve_lt_pace(183, 356)
        assert source == "laps" and pace != 293


class TestFetchThresholdLapsWindow:
    def test_stops_at_cutoff_without_paying_for_older_activities(self, monkeypatch):
        """Garmin returns newest-first: the walk must stop, not scan to the limit."""
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=2)).isoformat()
        old    = (date.today() - timedelta(days=400)).isoformat()
        splits_calls = []

        class FakeClient:
            def get_activities(self, start, limit):
                return [
                    {"activityId": 1, "startTimeLocal": f"{recent} 08:00:00",
                     "activityType": {"typeKey": "running"}},
                    {"activityId": 2, "startTimeLocal": f"{old} 08:00:00",
                     "activityType": {"typeKey": "running"}},
                    {"activityId": 3, "startTimeLocal": f"{old} 08:00:00",
                     "activityType": {"typeKey": "running"}},
                ]

            def get_activity_splits(self, aid):
                splits_calls.append(aid)
                return {"lapDTOs": [{"averageSpeed": 3.4, "averageHR": 175, "distance": 1000}]}

        monkeypatch.setattr(thresholds, "get_client", lambda: FakeClient())
        laps = thresholds.fetch_threshold_laps()
        assert splits_calls == [1], f"walked past the cutoff: {splits_calls}"
        assert len(laps) == 1
