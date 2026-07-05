from garminconnect.workout import (
    BaseWorkout,
    RunningWorkout,
    WorkoutSegment,
    ExecutableStep,
    RepeatGroup,
)

from garmin.client import get_client
from workouts.steps import build_running_steps, build_strength_steps


def create_and_upload_running_workout(
    workout_name: str,
    description: str | None,
    steps_raw: list[dict],
) -> dict:
    """Build steps, wrap in RunningWorkout, upload to Garmin.

    Returns {'workout_id': ..., 'step_count': ...}.
    """
    client = get_client()
    built_steps = build_running_steps(steps_raw)
    total_secs = sum(
        int(s.endConditionValue or 0)
        for s in built_steps
        if isinstance(s, ExecutableStep) and s.endCondition
        and s.endCondition.get("conditionTypeKey") == "time"
    )

    workout = RunningWorkout(
        workoutName=workout_name,
        description=description,
        estimatedDurationInSecs=max(total_secs, 60),
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
            workoutSteps=built_steps,
        )],
    )

    result = client.upload_running_workout(workout)
    workout_id = result.get("workoutId") or result.get("workout_id") or result.get("id")
    return {"workout_id": workout_id, "step_count": len(built_steps)}


def create_and_upload_strength_workout(
    workout_name: str,
    description: str | None,
    exercises: list[dict],
) -> dict:
    """Build steps, wrap in BaseWorkout, upload to Garmin.

    Returns {'workout_id': ..., 'exercise_summary': ..., 'unmapped': [...]}.
    """
    client = get_client()
    built_steps, unmapped = build_strength_steps(exercises)

    # Estimate duration: sum all inner step durations across repeat groups
    total_secs = 0
    for group in built_steps:
        if isinstance(group, RepeatGroup):
            inner_secs = sum(
                s.endConditionValue or 0
                for s in group.workoutSteps
                if isinstance(s, ExecutableStep)
                and s.endCondition
                and s.endCondition.get("conditionTypeKey") in ("time", "fixed.rest")
            )
            # For rep-based steps, estimate ~3s per rep
            inner_reps_secs = sum(
                (s.endConditionValue or 0) * 3
                for s in group.workoutSteps
                if isinstance(s, ExecutableStep)
                and s.endCondition
                and s.endCondition.get("conditionTypeKey") == "reps"
            )
            total_secs += (inner_secs + inner_reps_secs) * group.numberOfIterations

    strength_sport = {
        "sportTypeId": 5,
        "sportTypeKey": "strength_training",
        "displayOrder": 5,
    }

    workout = BaseWorkout(
        workoutName=workout_name,
        description=description,
        sportType=strength_sport,
        estimatedDurationInSecs=max(int(total_secs), 60),
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType=strength_sport,
            workoutSteps=built_steps,
        )],
    )

    result = client.upload_workout(workout.to_dict())
    workout_id = result.get("workoutId") or result.get("workout_id") or result.get("id")
    exercise_summary = ", ".join(
        f"{ex['name']} {ex.get('sets', 3)}×{ex['duration_seconds']}s"
        if 'duration_seconds' in ex and 'reps' not in ex
        else f"{ex['name']} {ex.get('sets', 3)}×{ex.get('reps', 10)}"
        for ex in exercises
    )
    return {"workout_id": workout_id, "exercise_summary": exercise_summary, "unmapped": unmapped}
