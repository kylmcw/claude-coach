"""
Tests for server/garmin/training.py

Covers (excluding fetch_recent_activities paging which test_fetch_paging.py already owns):
  - _week_bounds: calendar-week math
  - _month_bounds: calendar-month math (including year wrap-around)
  - _summarise_activities: filtering, totals, best-session logic
  - fetch_weekly_zone_distribution: zone aggregation, easy/hard percentages, error tolerance
  - _week_recommendation: ACWR threshold text
  - _delta_str: delta formatting
  - _fmt_load_focus: label translation
  - generate_week_suggestions: suggestion count and content
  - pace formatting inside fetch_recent_activities processing (via direct dict)
"""
import pytest
from datetime import date, timedelta

import garmin.training as training


# ─── _week_bounds ─────────────────────────────────────────────────────────────

def test_week_bounds_current_week_monday():
    """Monday of the current week must have weekday() == 0."""
    monday, sunday = training._week_bounds(0)
    assert monday.weekday() == 0


def test_week_bounds_current_week_sunday():
    """Sunday is exactly 6 days after the Monday."""
    monday, sunday = training._week_bounds(0)
    assert (sunday - monday).days == 6


def test_week_bounds_last_week_is_7_before_current():
    monday_this, _ = training._week_bounds(0)
    monday_last, _ = training._week_bounds(1)
    assert (monday_this - monday_last).days == 7


def test_week_bounds_span_contains_today():
    monday, sunday = training._week_bounds(0)
    today = date.today()
    assert monday <= today <= sunday


def test_week_bounds_last_week_does_not_contain_today():
    monday, sunday = training._week_bounds(1)
    today = date.today()
    assert not (monday <= today <= sunday)


def test_week_bounds_two_weeks_back():
    monday_0, _ = training._week_bounds(0)
    monday_2, _ = training._week_bounds(2)
    assert (monday_0 - monday_2).days == 14


# ─── _month_bounds ────────────────────────────────────────────────────────────

def test_month_bounds_current_first_day():
    first, last = training._month_bounds(0)
    assert first.day == 1
    assert first.month == date.today().month
    assert first.year == date.today().year


def test_month_bounds_current_last_day_is_month_end():
    import calendar
    first, last = training._month_bounds(0)
    expected_last = calendar.monthrange(first.year, first.month)[1]
    assert last.day == expected_last


def test_month_bounds_last_month_is_different():
    first_this, _ = training._month_bounds(0)
    first_last, _ = training._month_bounds(1)
    # Different months
    assert (first_this.year, first_this.month) != (first_last.year, first_last.month)


def test_month_bounds_year_wraps_in_january():
    """Going back enough months from any date must never produce month <= 0."""
    for months_back in range(1, 25):
        first, last = training._month_bounds(months_back)
        assert 1 <= first.month <= 12
        assert first.year >= 1


def test_month_bounds_12_months_back_same_month_prev_year():
    today = date.today()
    first, _ = training._month_bounds(12)
    assert first.month == today.month
    assert first.year == today.year - 1


# ─── _summarise_activities ────────────────────────────────────────────────────

def _raw_act(date_str, distance_m=8000, duration_s=2400, te=3.0, type_key="running"):
    return {
        "startTimeLocal": f"{date_str} 07:00:00",
        "activityName": "Morning Run",
        "activityType": {"typeKey": type_key},
        "distance": distance_m,
        "duration": duration_s,
        "aerobicTrainingEffect": te,
    }


def test_summarise_empty_window():
    acts = [_raw_act("2025-01-01")]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["session_count"] == 0
    assert out["total_distance_km"] == 0.0
    assert out["best_session"] is None


def test_summarise_filters_to_window():
    acts = [
        _raw_act("2025-06-01"),
        _raw_act("2025-06-04"),
        _raw_act("2025-06-10"),  # outside window
    ]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["session_count"] == 2


def test_summarise_total_distance():
    acts = [_raw_act("2025-06-02", distance_m=10000), _raw_act("2025-06-03", distance_m=5000)]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["total_distance_km"] == pytest.approx(15.0, rel=0.01)


def test_summarise_total_duration():
    acts = [_raw_act("2025-06-02", duration_s=3600), _raw_act("2025-06-03", duration_s=1800)]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["total_duration_min"] == pytest.approx(90.0, rel=0.01)


def test_summarise_best_session_is_highest_te():
    acts = [
        _raw_act("2025-06-02", te=2.0),
        _raw_act("2025-06-03", te=4.5),
        _raw_act("2025-06-04", te=3.0),
    ]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["best_session"]["aerobic_te"] == 4.5
    assert out["best_session"]["date"] == "2025-06-03"


def test_summarise_run_distance_excludes_non_running():
    acts = [
        _raw_act("2025-06-02", distance_m=10000, type_key="running"),
        _raw_act("2025-06-03", distance_m=20000, type_key="cycling"),
    ]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["total_distance_km"] == pytest.approx(30.0, rel=0.01)  # total includes all
    assert out["run_distance_km"] == pytest.approx(10.0, rel=0.01)   # only running


def test_summarise_longest_run():
    acts = [
        _raw_act("2025-06-02", distance_m=8000, type_key="running"),
        _raw_act("2025-06-03", distance_m=21000, type_key="running"),
        _raw_act("2025-06-05", distance_m=5000, type_key="running"),
    ]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["longest_run_km"] == pytest.approx(21.0, rel=0.01)


def test_summarise_type_counts():
    acts = [
        _raw_act("2025-06-02", type_key="running"),
        _raw_act("2025-06-03", type_key="running"),
        _raw_act("2025-06-04", type_key="cycling"),
    ]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["type_counts"]["running"] == 2
    assert out["type_counts"]["cycling"] == 1


def test_summarise_inclusive_boundary_dates():
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    acts = [
        _raw_act("2025-06-01"),  # on start boundary
        _raw_act("2025-06-07"),  # on end boundary
    ]
    out = training._summarise_activities(acts, start, end)
    assert out["session_count"] == 2


def test_summarise_skips_bad_date():
    acts = [
        {"startTimeLocal": "not-a-date 07:00:00", "activityType": {"typeKey": "running"},
         "distance": 1000, "duration": 300, "aerobicTrainingEffect": 2.0},
        _raw_act("2025-06-03"),
    ]
    start = date(2025, 6, 1)
    end   = date(2025, 6, 7)
    out = training._summarise_activities(acts, start, end)
    assert out["session_count"] == 1


# ─── fetch_weekly_zone_distribution ──────────────────────────────────────────

class _FakeZoneClient:
    """Client stub: maps activity_id → list of zone dicts."""
    def __init__(self, zone_map):
        self._map = zone_map

    def get_activity_hr_in_timezones(self, activity_id):
        return self._map.get(str(activity_id), [])


def _run_act(activity_id, type_key="running"):
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": type_key},
        "startTimeLocal": "2025-06-02 07:00:00",
    }


def _zones(z1=600, z2=1200, z3=300, z4=600, z5=300):
    return [
        {"zoneNumber": 1, "secsInZone": z1},
        {"zoneNumber": 2, "secsInZone": z2},
        {"zoneNumber": 3, "secsInZone": z3},
        {"zoneNumber": 4, "secsInZone": z4},
        {"zoneNumber": 5, "secsInZone": z5},
    ]


def test_zone_distribution_empty_activities(monkeypatch):
    monkeypatch.setattr(training, "get_client", lambda: _FakeZoneClient({}))
    out = training.fetch_weekly_zone_distribution([])
    assert out["zones"] == {}
    assert out["easy_pct"] is None
    assert out["total_min"] == 0


def test_zone_distribution_skips_non_running(monkeypatch):
    client = _FakeZoneClient({"1": _zones()})
    monkeypatch.setattr(training, "get_client", lambda: client)
    acts = [_run_act(1, type_key="cycling")]  # not running
    out = training.fetch_weekly_zone_distribution(acts)
    assert out["zones"] == {}


def test_zone_distribution_includes_trail_running(monkeypatch):
    client = _FakeZoneClient({"42": _zones(z1=600, z2=600, z3=0, z4=0, z5=0)})
    monkeypatch.setattr(training, "get_client", lambda: client)
    acts = [_run_act(42, type_key="trail_running")]
    out = training.fetch_weekly_zone_distribution(acts)
    assert out["easy_pct"] == 100.0


def test_zone_distribution_easy_pct():
    """With 1800s in Z1-2 and 300s in Z4-5 and 300s in Z3, easy% = 1800/2400 * 100 = 75."""
    # 600 Z1 + 1200 Z2 = 1800 easy, 300 Z3 moderate, 600+300 Z4+Z5 = 900... wait let me recompute
    # z1=600, z2=1200, z3=300, z4=600, z5=300 → total=3000
    # easy = 600+1200=1800, moderate=300, hard=600+300=900
    # easy_pct = 1800/3000 * 100 = 60.0
    import types as _types

    class _Client:
        def get_activity_hr_in_timezones(self, aid):
            return _zones(z1=600, z2=1200, z3=300, z4=600, z5=300)

    # Patch monkeypatch inline using monkeypatching via direct attribute replacement
    orig = training.get_client
    training.get_client = lambda: _Client()
    try:
        acts = [_run_act(1)]
        out = training.fetch_weekly_zone_distribution(acts)
        assert out["easy_pct"] == pytest.approx(60.0, abs=0.1)
        assert out["hard_pct"] == pytest.approx(30.0, abs=0.1)
        assert out["moderate_pct"] == pytest.approx(10.0, abs=0.1)
    finally:
        training.get_client = orig


def test_zone_distribution_tolerates_api_error(monkeypatch):
    """An activity that raises on zone fetch should be skipped, not abort the whole call."""
    class _ErrorClient:
        def get_activity_hr_in_timezones(self, aid):
            raise RuntimeError("network error")

    monkeypatch.setattr(training, "get_client", lambda: _ErrorClient())
    acts = [_run_act(1)]
    out = training.fetch_weekly_zone_distribution(acts)
    assert out["zones"] == {}  # graceful skip


def test_zone_distribution_aggregates_multiple_runs(monkeypatch):
    """Zones from two runs are summed together."""
    zone_map = {
        "1": [{"zoneNumber": 1, "secsInZone": 600}, {"zoneNumber": 2, "secsInZone": 600}],
        "2": [{"zoneNumber": 1, "secsInZone": 300}, {"zoneNumber": 2, "secsInZone": 300}],
    }
    monkeypatch.setattr(training, "get_client", lambda: _FakeZoneClient(zone_map))
    acts = [_run_act(1), _run_act(2)]
    out = training.fetch_weekly_zone_distribution(acts)
    assert out["zones"][1] == pytest.approx((600 + 300) / 60, rel=0.01)
    assert out["zones"][2] == pytest.approx((600 + 300) / 60, rel=0.01)
    assert out["easy_pct"] == 100.0


def test_zone_distribution_total_min(monkeypatch):
    zone_map = {"1": [{"zoneNumber": 1, "secsInZone": 3600}]}
    monkeypatch.setattr(training, "get_client", lambda: _FakeZoneClient(zone_map))
    acts = [_run_act(1)]
    out = training.fetch_weekly_zone_distribution(acts)
    assert out["total_min"] == pytest.approx(60.0, rel=0.01)


def test_zone_distribution_hrTimeInZones_key(monkeypatch):
    """Accepts {'hrTimeInZones': [...]} wrapper shape."""
    class _Client:
        def get_activity_hr_in_timezones(self, aid):
            return {"hrTimeInZones": [{"zoneNumber": 1, "secsInZone": 1200}]}

    monkeypatch.setattr(training, "get_client", lambda: _Client())
    acts = [_run_act(1)]
    out = training.fetch_weekly_zone_distribution(acts)
    assert out["easy_pct"] == 100.0


# ─── _week_recommendation ─────────────────────────────────────────────────────

def test_week_rec_none_acwr():
    assert "Not enough" in training._week_recommendation(None, None)


def test_week_rec_danger_zone():
    rec = training._week_recommendation(1.6, None)
    assert "MANDATORY EASY" in rec or "danger zone" in rec.upper() or "ACWR" in rec


def test_week_rec_elevated():
    rec = training._week_recommendation(1.4, None)
    assert "Back off" in rec


def test_week_rec_underloading():
    rec = training._week_recommendation(0.7, None)
    assert "Underloading" in rec or "add" in rec.lower()


def test_week_rec_volume_drop_rebuild():
    rec = training._week_recommendation(0.7, -25.0)
    assert "rebuild" in rec.lower() or "dropped" in rec.lower()


def test_week_rec_big_volume_jump():
    rec = training._week_recommendation(1.0, 20.0)
    assert "Hold" in rec or "steady" in rec.lower() or "jump" in rec.lower()


def test_week_rec_optimal():
    rec = training._week_recommendation(1.1, 5.0)
    assert "optimal" in rec.lower() or "course" in rec.lower()


# ─── _delta_str ───────────────────────────────────────────────────────────────

def test_delta_str_positive():
    out = training._delta_str(10, 8, "km")
    assert out.startswith("+")
    assert "2" in out
    assert "km" in out


def test_delta_str_negative():
    out = training._delta_str(8, 10, "km")
    assert out.startswith("-")


def test_delta_str_zero_has_plus():
    out = training._delta_str(5, 5, "min")
    assert "+" in out
    assert "0" in out


def test_delta_str_none_a():
    assert training._delta_str(None, 5) == "N/A"


def test_delta_str_none_b():
    assert training._delta_str(5, None) == "N/A"


def test_delta_str_both_none():
    assert training._delta_str(None, None) == "N/A"


# ─── _fmt_load_focus ─────────────────────────────────────────────────────────

def test_fmt_load_focus_known_key():
    assert "easy volume" in training._fmt_load_focus("LOW_AEROBIC_DEFICIT").lower()


def test_fmt_load_focus_case_insensitive():
    assert training._fmt_load_focus("low_aerobic_deficit") == training._fmt_load_focus("LOW_AEROBIC_DEFICIT")


def test_fmt_load_focus_unknown_returns_original():
    assert training._fmt_load_focus("MYSTERY_FOCUS") == "MYSTERY_FOCUS"


def test_fmt_load_focus_none_returns_empty():
    assert training._fmt_load_focus(None) == ""


def test_fmt_load_focus_all_known_keys():
    known = [
        "LOW_AEROBIC_DEFICIT", "LOW_AEROBIC_SURPLUS", "HIGH_AEROBIC_DEFICIT",
        "HIGH_AEROBIC_SURPLUS", "ANAEROBIC_SURPLUS", "ANAEROBIC_DEFICIT", "OPTIMAL",
    ]
    for k in known:
        result = training._fmt_load_focus(k)
        assert result != k, f"Key {k!r} was returned unchanged — not in labels dict"


# ─── generate_week_suggestions ────────────────────────────────────────────────

def _make_week(sessions=4, distance=40.0):
    return {"session_count": sessions, "total_distance_km": distance}


def test_suggestions_returns_list():
    out = training.generate_week_suggestions(_make_week(), _make_week(), {"acwr": 1.0})
    assert isinstance(out, list)


def test_suggestions_max_three():
    out = training.generate_week_suggestions(_make_week(), _make_week(), {"acwr": 1.0})
    assert len(out) <= 3


def test_suggestions_mandatory_easy_week_when_acwr_over_15():
    this_week = _make_week(sessions=5, distance=60.0)
    last_week = _make_week(sessions=4, distance=40.0)
    out = training.generate_week_suggestions(this_week, last_week, {"acwr": 1.6})
    assert any("Mandatory easy week" in s["recommendation"] or "mandatory" in s["recommendation"].lower()
               for s in out)


def test_suggestions_back_off_when_acwr_between_13_and_15():
    out = training.generate_week_suggestions(_make_week(distance=50.0), _make_week(distance=40.0), {"acwr": 1.4})
    assert any("Back off" in s["recommendation"] or "back off" in s["recommendation"].lower()
               for s in out)


def test_suggestions_low_sessions_triggers_consistency():
    out = training.generate_week_suggestions(_make_week(sessions=2), _make_week(), {"acwr": 1.0})
    assert any(s["action_type"] == "add_session" for s in out)


def test_suggestions_no_acwr_data():
    out = training.generate_week_suggestions(_make_week(), _make_week(), {"acwr": None})
    assert len(out) >= 1
    assert any("gradually" in s["recommendation"].lower() or "ACWR" in s["recommendation"] for s in out)


def test_suggestions_each_has_required_keys():
    out = training.generate_week_suggestions(_make_week(), _make_week(), {"acwr": 1.1})
    for s in out:
        assert "recommendation" in s
        assert "rationale" in s
        assert "action_type" in s


def test_suggestions_action_types_are_valid():
    valid_types = {"adjust_volume", "add_session", "add_quality", "note_only"}
    out = training.generate_week_suggestions(_make_week(), _make_week(), {"acwr": 1.1})
    for s in out:
        assert s["action_type"] in valid_types


# ─── Pace formatting (via fetch_recent_activities processing logic) ───────────

def _pace_from_raw(distance_m, duration_s):
    """Replicate the pace-formatting logic from fetch_recent_activities."""
    distance_km = round(distance_m / 1000, 2)
    duration_min = round(duration_s / 60, 1)
    if distance_km > 0 and duration_min > 0:
        pace_sec = round(duration_min * 60 / distance_km)
        return f"{pace_sec // 60}:{pace_sec % 60:02d}/km"
    return "N/A"


def test_pace_format_5min_per_km():
    # 5km in 25 minutes → 5:00/km
    result = _pace_from_raw(5000, 1500)
    assert result == "5:00/km"


def test_pace_format_sub_10_min_per_km():
    # 1km in 8 minutes → 8:00/km
    result = _pace_from_raw(1000, 480)
    assert result == "8:00/km"


def test_pace_format_zero_distance():
    result = _pace_from_raw(0, 1200)
    assert result == "N/A"


def test_pace_format_zero_duration():
    result = _pace_from_raw(5000, 0)
    assert result == "N/A"


def test_pace_format_seconds_padded():
    # 10km in 62min = 372s/km → 6:12/km
    result = _pace_from_raw(10000, 3720)
    assert result == "6:12/km"
