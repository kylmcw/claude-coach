from garminconnect.workout import (
    ExecutableStep,
    RepeatGroup,
    StepType,
    ConditionType,
    TargetType,
    create_warmup_step,
    create_cooldown_step,
    create_recovery_step,
    create_repeat_group,
)

from db.exercises import lookup_garmin_exercise
from db.exercises import get_exercise_defaults


def _build_target(step: dict) -> tuple[dict, dict]:
    """
    Return (targetType dict, extra_kwargs) for an ExecutableStep based on optional
    target fields in the step dict.

    Supported inputs:
      target_hr_zone: int 1–5        → TargetType.HEART_RATE (zone number)
      target_hr_low / target_hr_high → TargetType.HEART_RATE (bpm range)
      target_pace_slow / target_pace_fast  (sec/km)
                                     → TargetType.SPEED (speed.zone, m/s range)

    Pace sec/km → speed m/s:  speed = 1000 / pace_sec
    Garmin convention: targetValueOne ≤ targetValueTwo (slower→faster in m/s).
    """
    # HR zone
    if "target_hr_zone" in step:
        zone = int(step["target_hr_zone"])
        return (
            {"workoutTargetTypeId": TargetType.HEART_RATE,
             "workoutTargetTypeKey": "heart.rate.zone",
             "displayOrder": 4},
            {"zoneNumber": zone},
        )

    # HR bpm range
    if "target_hr_low" in step or "target_hr_high" in step:
        lo = float(step.get("target_hr_low") or step.get("target_hr_high"))
        hi = float(step.get("target_hr_high") or step.get("target_hr_low"))
        return (
            {"workoutTargetTypeId": TargetType.HEART_RATE,
             "workoutTargetTypeKey": "heart.rate.zone",
             "displayOrder": 4},
            {"targetValueOne": lo, "targetValueTwo": hi},
        )

    # Pace band (sec/km) → speed zone (m/s)
    if "target_pace_slow" in step or "target_pace_fast" in step:
        slow_sec = step.get("target_pace_slow")
        fast_sec = step.get("target_pace_fast")
        # Mirror missing bound: Garmin requires both targetValueOne and targetValueTwo.
        if slow_sec and not fast_sec:
            fast_sec = max(slow_sec - 20, 1)
        elif fast_sec and not slow_sec:
            slow_sec = fast_sec + 20
        # slower pace = lower m/s (valueOne), faster pace = higher m/s (valueTwo)
        lo_ms = round(1000 / slow_sec, 4)
        hi_ms = round(1000 / fast_sec, 4)
        return (
            {"workoutTargetTypeId": TargetType.SPEED,
             "workoutTargetTypeKey": "speed.zone",
             "displayOrder": 5},
            {"targetValueOne": lo_ms, "targetValueTwo": hi_ms},
        )

    # No target
    return (
        {"workoutTargetTypeId": TargetType.NO_TARGET,
         "workoutTargetTypeKey": "no.target",
         "displayOrder": 1},
        {},
    )


def build_running_steps(steps: list[dict]) -> list:
    """
    Recursively convert a list of step dicts into garminconnect step objects.

    Supported step types:
      warmup    – {"type": "warmup",    "duration_minutes": 10}
      easy      – {"type": "easy",      "duration_minutes": 20}
      interval  – {"type": "interval",  "duration_minutes": 2}   (time-based)
                  {"type": "interval",  "distance_meters":  400} (distance-based)
      recovery  – {"type": "recovery",  "duration_minutes": 1.5}
      cooldown  – {"type": "cooldown",  "duration_minutes": 10}
      repeat    – {"type": "repeat",    "iterations": 5, "steps": [...]}
    """
    result = []
    for i, step in enumerate(steps, start=1):
        step_type = step.get("type", "").lower()

        if step_type == "repeat":
            nested = build_running_steps(step["steps"])
            result.append(create_repeat_group(
                iterations=int(step["iterations"]),
                workout_steps=nested,
                step_order=i,
            ))

        elif step_type == "warmup":
            secs = float(step["duration_minutes"]) * 60
            result.append(create_warmup_step(duration_seconds=secs, step_order=i))

        elif step_type == "cooldown":
            secs = float(step["duration_minutes"]) * 60
            result.append(create_cooldown_step(duration_seconds=secs, step_order=i))

        elif step_type == "recovery":
            secs = float(step["duration_minutes"]) * 60
            result.append(create_recovery_step(duration_seconds=secs, step_order=i))

        elif step_type in ("interval", "easy"):
            if "distance_meters" in step:
                end_condition = {
                    "conditionTypeId": ConditionType.DISTANCE,
                    "conditionTypeKey": "distance",
                    "displayOrder": 1,
                    "displayable": True,
                }
                end_value = float(step["distance_meters"])
            else:
                end_condition = {
                    "conditionTypeId": ConditionType.TIME,
                    "conditionTypeKey": "time",
                    "displayOrder": 2,
                    "displayable": True,
                }
                end_value = float(step["duration_minutes"]) * 60

            target_type, target_kwargs = _build_target(step)

            result.append(ExecutableStep(
                stepOrder=i,
                stepType={
                    "stepTypeId": StepType.INTERVAL,
                    "stepTypeKey": "interval",
                    "displayOrder": 3,
                },
                endCondition=end_condition,
                endConditionValue=end_value,
                targetType=target_type,
                **target_kwargs,
            ))

        else:
            raise ValueError(f"Unknown running step type: '{step_type}'")

    return result


def build_strength_steps(exercises: list[dict]) -> tuple[list, list[str]]:
    """
    Convert a list of exercise dicts into structured Garmin workout steps.

    Each exercise becomes a RepeatGroup (iterations = sets) containing:
      1. An exercise step with rep-based or time-based end condition + category/exerciseName
      2. A rest step between sets

    Rep-based:  {"name": "Squat", "sets": 4, "reps": 8, "rest_seconds": 90}
    Time-based: {"name": "Plank", "sets": 3, "duration_seconds": 45, "rest_seconds": 60}

    Returns (steps, unmapped_names) — unmapped_names lists exercises with no Garmin mapping.
    Unmapped exercises are still created with null category/exerciseName (Garmin accepts this).
    """
    # Pre-fetch all defaults — two indexes for fast lookup
    _raw_defaults   = get_exercise_defaults()
    all_defaults    = {d["name"].lower(): d for d in _raw_defaults}
    garmin_defaults = {d["garmin_exercise_name"]: d
                       for d in _raw_defaults if d.get("garmin_exercise_name")}

    def _token_overlap(a: str, b: str) -> float:
        """Jaccard similarity on word tokens (ignoring parentheses)."""
        clean = str.maketrans("", "", "()")
        ta = set(a.lower().translate(clean).split())
        tb = set(b.lower().translate(clean).split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    def _find_default(exercise_name: str) -> dict:
        key = exercise_name.strip().lower()

        # 1. Exact display-name match
        if key in all_defaults:
            return all_defaults[key]

        # 2. Garmin canonical name match — handles word order / synonym variations
        _, g_name = lookup_garmin_exercise(exercise_name)
        if g_name is not None and g_name in garmin_defaults:
            return garmin_defaults[g_name]

        # 3. Token overlap on display names (Jaccard ≥ 0.5)
        best, best_score = {}, 0.0
        for stored_key, stored_val in all_defaults.items():
            score = _token_overlap(key, stored_key)
            if score > best_score:
                best, best_score = stored_val, score
        if best_score >= 0.5:
            return best

        return {}

    steps    = []
    unmapped = []
    order    = 1

    for ex in exercises:
        name       = ex["name"]
        rest_secs  = float(ex.get("rest_seconds", 60))
        duration   = ex.get("duration_seconds")

        # Merge defaults: explicit args take priority over stored defaults
        default    = _find_default(name)
        sets       = int(ex.get("sets")  or default.get("sets")  or 3)
        reps       = ex.get("reps")      or default.get("reps")
        weight_kg  = ex.get("weight_kg") or default.get("weight_kg")

        # Use pre-resolved Garmin mapping from defaults if available, else look up
        if default.get("garmin_category") and default.get("garmin_exercise_name"):
            category      = default["garmin_category"]
            exercise_name = default["garmin_exercise_name"]
        else:
            category, exercise_name = lookup_garmin_exercise(name)
            if category is None:
                unmapped.append(name)

        weight_label = f" — {weight_kg}kg" if weight_kg is not None else ""

        # Determine end condition: time-based or rep-based
        if duration is not None:
            dur_val = float(duration)
            end_condition = {
                "conditionTypeId": ConditionType.TIME,
                "conditionTypeKey": "time",
                "displayOrder": 2,
                "displayable": True,
            }
            end_condition_value = dur_val
            step_desc = f"{name}{weight_label} ({int(dur_val)}s)"
        else:
            reps_val = int(reps) if reps is not None else 10
            end_condition = {
                "conditionTypeId": 10,
                "conditionTypeKey": "reps",
                "displayOrder": 10,
                "displayable": True,
            }
            end_condition_value = float(reps_val)
            step_desc = f"{name}{weight_label} ({reps_val} reps)"

        # weightValue in kg (display unit), weightUnit mirrors the unit object
        weight_kwargs: dict = {}
        if weight_kg is not None:
            weight_kwargs["weightValue"] = float(weight_kg)
            weight_kwargs["weightUnit"]  = {"unitKey": "kilogram"}

        exercise_step = ExecutableStep(
            stepOrder=1,
            stepType={
                "stepTypeId": StepType.INTERVAL,
                "stepTypeKey": "interval",
                "displayOrder": 3,
            },
            endCondition=end_condition,
            endConditionValue=end_condition_value,
            targetType={
                "workoutTargetTypeId": TargetType.NO_TARGET,
                "workoutTargetTypeKey": "no.target",
                "displayOrder": 1,
            },
            category=category,
            exerciseName=exercise_name,
            description=step_desc,
            **weight_kwargs,
        )

        rest_step = ExecutableStep(
            stepOrder=2,
            stepType={
                "stepTypeId": StepType.REST,
                "stepTypeKey": "rest",
                "displayOrder": 5,
            },
            endCondition={
                "conditionTypeId": 8,
                "conditionTypeKey": "fixed.rest",
                "displayOrder": 8,
                "displayable": True,
            },
            endConditionValue=rest_secs,
            targetType={
                "workoutTargetTypeId": TargetType.NO_TARGET,
                "workoutTargetTypeKey": "no.target",
                "displayOrder": 1,
            },
        )

        group = RepeatGroup(
            stepOrder=order,
            stepType={
                "stepTypeId": StepType.REPEAT,
                "stepTypeKey": "repeat",
                "displayOrder": 6,
            },
            numberOfIterations=sets,
            workoutSteps=[exercise_step, rest_step],
            endCondition={
                "conditionTypeId": ConditionType.ITERATIONS,
                "conditionTypeKey": "iterations",
                "displayOrder": 7,
                "displayable": False,
            },
            endConditionValue=float(sets),
        )

        steps.append(group)
        order += 1

    return steps, unmapped
