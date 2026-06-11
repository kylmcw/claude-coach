import pytest

import workouts.steps as steps


def test_build_target_pace_band_orders_slow_to_fast():
    ttype, kwargs = steps._build_target({"target_pace_slow": 360, "target_pace_fast": 345})
    assert ttype["workoutTargetTypeKey"] == "speed.zone"
    # slower pace (sec/km) -> lower m/s -> targetValueOne, must be <= targetValueTwo
    assert kwargs["targetValueOne"] <= kwargs["targetValueTwo"]
    assert kwargs["targetValueOne"] == round(1000 / 360, 4)
    assert kwargs["targetValueTwo"] == round(1000 / 345, 4)


def test_build_target_single_pace_bound_is_mirrored():
    _, kwargs = steps._build_target({"target_pace_slow": 360})
    assert "targetValueOne" in kwargs and "targetValueTwo" in kwargs
    assert kwargs["targetValueOne"] <= kwargs["targetValueTwo"]


def test_build_target_hr_zone():
    ttype, kwargs = steps._build_target({"target_hr_zone": 3})
    assert ttype["workoutTargetTypeKey"] == "heart.rate.zone"
    assert kwargs == {"zoneNumber": 3}


def test_build_target_none():
    ttype, kwargs = steps._build_target({})
    assert ttype["workoutTargetTypeKey"] == "no.target"
    assert kwargs == {}


def test_build_running_steps_flat_count():
    out = steps.build_running_steps([
        {"type": "warmup", "duration_minutes": 10},
        {"type": "easy", "duration_minutes": 20},
        {"type": "cooldown", "duration_minutes": 10},
    ])
    assert len(out) == 3


def test_build_running_steps_repeat_is_single_group():
    out = steps.build_running_steps([
        {"type": "warmup", "duration_minutes": 10},
        {"type": "repeat", "iterations": 5, "steps": [
            {"type": "interval", "distance_meters": 400},
            {"type": "recovery", "duration_minutes": 2},
        ]},
        {"type": "cooldown", "duration_minutes": 10},
    ])
    assert len(out) == 3  # warmup, one repeat group, cooldown


def test_build_running_steps_unknown_type_raises():
    with pytest.raises(ValueError):
        steps.build_running_steps([{"type": "sprintz", "duration_minutes": 1}])


def test_build_strength_reports_unmapped(monkeypatch):
    monkeypatch.setattr(steps, "get_exercise_defaults", lambda: [])
    monkeypatch.setattr(steps, "lookup_garmin_exercise", lambda name: (None, None))
    built, unmapped = steps.build_strength_steps([{"name": "Foo Curl", "sets": 3, "reps": 10}])
    assert len(built) == 1
    assert unmapped == ["Foo Curl"]


def test_build_strength_mapped_has_no_unmapped(monkeypatch):
    monkeypatch.setattr(steps, "get_exercise_defaults", lambda: [])
    monkeypatch.setattr(steps, "lookup_garmin_exercise", lambda name: ("CHEST", "BARBELL_BENCH_PRESS"))
    built, unmapped = steps.build_strength_steps([{"name": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60}])
    assert len(built) == 1
    assert unmapped == []
