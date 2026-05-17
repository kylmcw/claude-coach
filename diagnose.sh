#!/usr/bin/env zsh
# Run this from the garmin-coach directory to see the exact startup error.
set -e
cd "$(dirname "$0")"

echo "=== Python version ==="
.venv/bin/python3 --version

echo ""
echo "=== Import check ==="
.venv/bin/python3 - << 'PYEOF'
import sys
print("sys.path:", sys.path[:3])

try:
    from garminconnect.workout import (
        RunningWorkout, FitnessEquipmentWorkout, WorkoutSegment,
        ExecutableStep, RepeatGroup, StepType, ConditionType,
        TargetType, create_warmup_step, create_cooldown_step,
        create_interval_step, create_recovery_step, create_repeat_group,
    )
    print("garminconnect.workout: OK")
except Exception as e:
    print(f"garminconnect.workout FAILED: {e}")
    sys.exit(1)

try:
    import server.main
    print("server.main: OK")
except Exception as e:
    print(f"server.main FAILED: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print("All imports OK — server should start cleanly.")
PYEOF
