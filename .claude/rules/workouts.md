---
paths:
  - "server/workouts/steps.py"
  - "server/workouts/workouts.py"
  - "server/workouts/validate.py"
---

# Workout Builder Rules

## Running Steps

`build_running_steps(steps)` converts step dicts into garminconnect objects.

Supported types:
- `warmup` / `cooldown` — `duration_minutes`
- `easy` / `interval` — `duration_minutes` OR `distance_meters`; optional `target_hr_zone`, `target_hr_low/high`, `target_pace_slow/fast` (sec/km)
- `recovery` — `duration_minutes`
- `repeat` — `iterations` + nested `steps: [...]`

Pace sec/km → speed m/s: `speed = 1000 / pace_sec`. Garmin requires both `targetValueOne` ≤ `targetValueTwo` (slower m/s → faster m/s).

## Strength Steps

`build_strength_steps(exercises)` returns `(steps, unmapped_names)`.

Each exercise becomes a `RepeatGroup` (iterations = sets) containing:
1. `ExecutableStep` — the work step (rep-based or time-based)
2. `ExecutableStep` (rest) — fixed rest between sets

Exercise dict: `{name, sets?, reps?, duration_seconds?, rest_seconds?, weight_kg?}`

## Weight Fields on ExecutableStep

Garmin's confirmed field names (verified from live API response):
```python
weight_kwargs["weightValue"] = float(weight_kg)   # in kg
weight_kwargs["weightUnit"]  = {"unitKey": "kilogram"}
# Pass as **weight_kwargs to ExecutableStep — ConfigDict(extra="allow") lets them through
```

## Exercise Mapping

`lookup_garmin_exercise(name)` in `db/exercises.py` queries the SQLite `exercise_aliases` + `garmin_exercises` tables.
Returns `(category, exercise_name)` or `(None, None)` if unmapped.
Garmin accepts `null` category/exerciseName — unmapped exercises still upload; they just won't appear in Garmin's exercise library.

When `create_and_upload_strength_workout` returns `unmapped` names, the dispatcher in `main.py` warns the user and suggests `set_exercise_defaults` with explicit Garmin mapping.

## RepeatGroup Structure

```python
RepeatGroup(
    stepOrder=order,
    stepType={"stepTypeId": StepType.REPEAT, "stepTypeKey": "repeat", "displayOrder": 6},
    numberOfIterations=sets,
    workoutSteps=[exercise_step, rest_step],
    endCondition={"conditionTypeId": ConditionType.ITERATIONS, "conditionTypeKey": "iterations", ...},
    endConditionValue=float(sets),
)
```
