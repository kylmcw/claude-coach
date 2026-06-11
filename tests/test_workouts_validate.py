import pytest

import workouts.validate as v


def test_total_distance_km_with_nested_repeat():
    steps = [
        {"type": "warmup", "duration_minutes": 10},
        {"type": "interval", "distance_meters": 800},
        {"type": "repeat", "iterations": 5, "steps": [
            {"type": "interval", "distance_meters": 400},
            {"type": "recovery", "duration_minutes": 2},
        ]},
    ]
    assert v.total_distance_km(steps) == pytest.approx(0.8 + 5 * 0.4)


def test_total_distance_km_ignores_time_only_steps():
    assert v.total_distance_km([{"type": "easy", "duration_minutes": 30}]) == 0.0


def test_collect_step_types_recurses_into_repeat():
    steps = [
        {"type": "warmup", "duration_minutes": 10},
        {"type": "repeat", "iterations": 4, "steps": [
            {"type": "interval", "duration_minutes": 3},
            {"type": "recovery", "duration_minutes": 2},
        ]},
    ]
    types = v._collect_step_types(steps)
    assert "interval" in types     # the intensity gate must see this despite nesting
    assert "warmup" in types
    assert "repeat" not in types   # container type itself isn't collected


@pytest.mark.parametrize("week,total,expected", [
    (1, 12, "Base"), (4, 12, "Development"), (7, 12, "Specific"),
    (10, 12, "Taper"), (12, 12, "Race Week"),
])
def test_validate_phase_matches_plan(week, total, expected):
    # validate.py keeps its own copy of get_plan_phase (standalone hook); guard it too.
    assert v.get_plan_phase(week, total) == expected
