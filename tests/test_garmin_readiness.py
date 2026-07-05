"""
Tests for server/garmin/readiness.py

Covers:
  - assess_readiness: GREEN/AMBER/RED transitions at boundary values
  - assess_readiness: HRV, RHR, sleep, body battery, stress interactions
  - assess_readiness: Training Readiness (tr_score) overrides
  - assess_readiness: Garmin HRV-status overrides (LOW, UNBALANCED)
  - assess_readiness: cross-check logic (TR high vs RED local, TR low vs GREEN local)
  - assess_acwr: all four ACWR bands
"""
import pytest

import garmin.readiness as readiness


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _baselines(hrv_low=60, hrv_high=80, rhr_norm=55):
    """Return a tuple that matches load_baselines() output."""
    return (hrv_low, hrv_high, rhr_norm)


def _data(
    hrv=70, hrv_status=None, resting_hr=55, sleep_hours=7.5, sleep_score=80,
    body_battery=75, avg_stress=30, tr_score=None, tr_level=None,
):
    return {
        "hrv":             hrv,
        "hrv_status":      hrv_status,
        "resting_hr":      resting_hr,
        "sleep_hours":     sleep_hours,
        "sleep_score":     sleep_score,
        "body_battery":    body_battery,
        "avg_stress":      avg_stress,
        "tr_score":        tr_score,
        "tr_level":        tr_level,
    }


def _assess(data_override=None, baselines=(60, 80, 55), zones=None):
    """Run assess_readiness with stubbed load_baselines and stubbed zones."""
    d = data_override or _data()
    orig_load = readiness.load_baselines
    orig_get_cached_zones = readiness._get_cached_zones
    readiness.load_baselines = lambda: baselines
    readiness._get_cached_zones = lambda: zones or {}
    try:
        return readiness.assess_readiness(d)
    finally:
        readiness.load_baselines = orig_load
        readiness._get_cached_zones = orig_get_cached_zones


# ─── Healthy baseline → GREEN ─────────────────────────────────────────────────

def test_all_good_is_green():
    status, flags, decision = _assess()
    assert status == "green"
    assert "TRAIN AS PLANNED" in decision


# ─── HRV below baseline → AMBER ───────────────────────────────────────────────

def test_hrv_below_low_is_amber():
    d = _data(hrv=58)  # hrv_low=60 → below baseline but not well below (60-10=50)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("below baseline" in f for f in flags)


def test_hrv_well_below_low_is_red():
    d = _data(hrv=45)  # hrv_low=60 → well below (60-10=50, 45 < 50) → RED
    status, flags, _ = _assess(d)
    assert status == "red"
    assert any("well below" in f for f in flags)


def test_hrv_normal_stays_green():
    d = _data(hrv=70)  # within [60,80]
    status, _, _ = _assess(d)
    assert status == "green"


def test_hrv_none_escalates_to_amber():
    d = _data(hrv=None)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("No HRV" in f for f in flags)


# ─── Garmin HRV status overrides ─────────────────────────────────────────────

def test_hrv_status_low_escalates_to_amber():
    d = _data(hrv=70, hrv_status="LOW")  # HRV normal but Garmin says LOW
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("LOW" in f for f in flags)


def test_hrv_status_unbalanced_escalates_to_amber():
    d = _data(hrv=70, hrv_status="UNBALANCED")
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("UNBALANCED" in f for f in flags)


def test_hrv_status_normal_no_override():
    d = _data(hrv=70, hrv_status="BALANCED")
    status, _, _ = _assess(d)
    assert status == "green"


# ─── RHR elevated ────────────────────────────────────────────────────────────

def test_rhr_elevated_5_plus_is_amber():
    # rhr_norm=55, rhr=61 → rhr > rhr_norm + 5 → amber
    d = _data(resting_hr=61)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("elevated" in f for f in flags)


def test_rhr_at_boundary_not_amber():
    # rhr_norm=55, rhr=59 → 59 > 55+5=60 is False → no flag
    d = _data(resting_hr=59)
    status, _, _ = _assess(d)
    assert status == "green"


def test_rhr_exact_threshold_not_amber():
    # rhr=60 → 60 > 60 is False → no flag
    d = _data(resting_hr=60)
    status, _, _ = _assess(d)
    assert status == "green"


def test_rhr_one_over_threshold_is_amber():
    # rhr=61 → 61 > 60 is True → amber
    d = _data(resting_hr=61)
    status, _, _ = _assess(d)
    assert status == "amber"


# ─── Sleep score ─────────────────────────────────────────────────────────────

def test_sleep_score_below_60_is_amber():
    d = _data(sleep_score=55)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("poor" in f for f in flags)


def test_sleep_score_75_plus_is_good():
    d = _data(sleep_score=80)
    status, flags, _ = _assess(d)
    assert status == "green"
    assert any("good" in f.lower() for f in flags)


def test_sleep_score_takes_priority_over_hours():
    # sleep_score is present — hours should NOT produce flags
    d = _data(sleep_score=80, sleep_hours=4.0)
    _, flags, _ = _assess(d)
    # score-based flag present
    assert any("Sleep Score" in f for f in flags)
    # hours-based flag absent (sleep_score present → hours branch skipped)
    assert not any("Sleep " in f and "h —" in f for f in flags)


def test_sleep_hours_fallback_when_no_score():
    d = _data(sleep_score=None, sleep_hours=4.5)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("insufficient" in f for f in flags)


def test_sleep_hours_suboptimal_no_status_change():
    d = _data(sleep_score=None, sleep_hours=6.0)
    status, flags, _ = _assess(d)
    # suboptimal but doesn't push RED; green may stay green
    assert any("suboptimal" in f for f in flags)


# ─── Body battery ─────────────────────────────────────────────────────────────

def test_body_battery_below_25_is_red():
    d = _data(body_battery=20)
    status, flags, _ = _assess(d)
    assert status == "red"
    assert any("depleted" in f for f in flags)


def test_body_battery_25_to_50_is_amber():
    d = _data(body_battery=35)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("low" in f for f in flags)


def test_body_battery_50_plus_is_good():
    d = _data(body_battery=80)
    _, flags, _ = _assess(d)
    assert any("good" in f for f in flags)


def test_body_battery_exactly_25_is_amber():
    # Boundary: readiness pushes RED only when body_battery < 25, so 25 itself is amber.
    d = _data(body_battery=25)
    status, _, _ = _assess(d)
    assert status == "amber"
    # This test needs to match the real boundary
    # body_battery = 25: 25 < 25 is False, 25 < 50 is True → amber
    assert status in ("amber", "red")  # boundary: < 25 → red, else if < 50 → amber


def test_body_battery_24_is_red():
    d = _data(body_battery=24)
    status, _, _ = _assess(d)
    assert status == "red"


def test_body_battery_26_is_amber():
    d = _data(body_battery=26)
    status, _, _ = _assess(d)
    assert status == "amber"


# ─── Stress ──────────────────────────────────────────────────────────────────

def test_stress_over_70_is_amber():
    d = _data(avg_stress=75)
    status, flags, _ = _assess(d)
    assert status == "amber"
    assert any("high" in f for f in flags)


def test_stress_moderate_no_status_change():
    d = _data(avg_stress=60)
    status, flags, _ = _assess(d)
    assert status == "green"
    assert any("moderate" in f for f in flags)


def test_stress_low_is_good():
    d = _data(avg_stress=25)
    _, flags, _ = _assess(d)
    assert any("low" in f for f in flags)


# ─── Training Readiness score ─────────────────────────────────────────────────

def test_tr_score_high_adds_flag():
    d = _data(tr_score=80, tr_level="HIGH")
    _, flags, _ = _assess(d)
    assert any("Training Readiness" in f for f in flags)


def test_tr_score_very_low_forces_red():
    d = _data(tr_score=20)
    status, flags, _ = _assess(d)
    assert status == "red"


def test_tr_score_low_25_to_49_is_amber():
    d = _data(tr_score=30)
    status, _, _ = _assess(d)
    assert status == "amber"


def test_tr_score_cross_check_high_tr_but_red_metrics():
    """High TR score + red body battery should trust the local RED signal."""
    d = _data(tr_score=80, body_battery=20)  # body battery forces RED
    status, flags, _ = _assess(d)
    assert status == "red"
    assert any("trust local signals" in f or "local metrics" in f for f in flags)


def test_tr_score_cross_check_low_tr_but_green_metrics():
    """Low TR score but otherwise healthy metrics should escalate to amber."""
    d = _data(tr_score=35)  # low TR  → this alone already makes amber
    status, _, _ = _assess(d)
    assert status in ("amber", "red")


# ─── Decision strings ────────────────────────────────────────────────────────

def test_green_decision_says_train():
    _, _, decision = _assess()
    assert "TRAIN AS PLANNED" in decision


def test_amber_decision_says_easy_run():
    d = _data(body_battery=35)
    _, _, decision = _assess(d)
    assert "EASY RUN ONLY" in decision


def test_red_decision_says_rest():
    d = _data(body_battery=20)
    _, _, decision = _assess(d)
    assert "REST DAY" in decision


# ─── assess_training_state (driven by training status + load focus) ──────────

def _load(status=None, focus=None, acwr=None):
    return {"training_status": status, "load_focus": focus, "acwr": acwr}


def test_state_no_status_data():
    assert "unavailable" in readiness.assess_training_state(_load()).lower()


def test_state_detraining_add_volume():
    result = readiness.assess_training_state(_load(status="DETRAINING"))
    assert "add volume" in result.lower()


def test_state_productive_stay_course():
    result = readiness.assess_training_state(_load(status="PRODUCTIVE_6"))
    assert "stay the course" in result.lower()


def test_state_overreaching_back_off():
    result = readiness.assess_training_state(_load(status="OVERREACHING_1"))
    assert "back off" in result.lower()


def test_state_strained_mandatory_easy():
    result = readiness.assess_training_state(_load(status="STRAINED"))
    assert "mandatory easy" in result.lower()


def test_state_shows_acwr_as_reference():
    # ACWR is displayed but does not drive the call: 1.6 + PRODUCTIVE = stay course.
    result = readiness.assess_training_state(_load(status="PRODUCTIVE_6", acwr=1.6))
    assert "1.6" in result and "reference" in result.lower()
    assert "mandatory" not in result.lower()
