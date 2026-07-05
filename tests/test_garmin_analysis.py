"""
Tests for garmin.analysis weekly-context additions:
  - build_week_context: summarises last-7-day sessions + status, flags quality done
  - format_run_analysis: threads week_context into the output
"""
import garmin.analysis as analysis


def _act(date_str, typ="running", dist_km=8.0, aero=2.0, anaero=0.0):
    return {"date": date_str, "type": typ, "distance_km": dist_km,
            "aerobic_te": aero, "anaerobic_te": anaero}


def test_week_context_empty_when_no_activities():
    assert analysis.build_week_context([], {"training_status": "PRODUCTIVE_6"}) == ""


def test_week_context_reports_session_count_and_status():
    acts = [_act("2026-07-01"), _act("2026-07-03")]
    out = analysis.build_week_context(acts, {"training_status": "PRODUCTIVE_6", "load_focus": "AEROBIC_LOW_FOCUS"})
    assert "2 session" in out
    assert "PRODUCTIVE_6" in out
    assert "AEROBIC_LOW_FOCUS" in out


def test_week_context_flags_quality_already_done():
    # A session with real anaerobic TE counts as hard/quality.
    acts = [_act("2026-07-01", anaero=2.5), _act("2026-07-02", aero=2.0)]
    out = analysis.build_week_context(acts, {})
    assert "Quality/hard already done: 1" in out


def test_week_context_notes_when_no_quality():
    acts = [_act("2026-07-01", aero=2.0), _act("2026-07-02", aero=1.5)]
    out = analysis.build_week_context(acts, {})
    assert "No hard/quality sessions" in out


def _min_run_data():
    return {
        "activity_id": 1, "activity_name": "Run", "activity_type": "running",
        "date": "2026-07-05", "distance_km": 8.0, "duration_min": 40.0,
        "avg_pace": "5:00/km", "avg_hr": 150, "max_hr": 165, "calories": 400,
        "aerobic_te": 2.5, "anaerobic_te": 0.0,
        "splits": [], "splits_source": "distance", "hr_zones": [],
    }


def test_format_run_analysis_includes_week_context():
    ctx = "THIS WEEK SO FAR (last 7d): 3 session(s), 24.0 km running."
    out = analysis.format_run_analysis(_min_run_data(), plan_context="", week_context=ctx)
    assert ctx in out


def test_format_run_analysis_omits_empty_week_context():
    out = analysis.format_run_analysis(_min_run_data(), plan_context="", week_context="")
    assert "THIS WEEK SO FAR" not in out
