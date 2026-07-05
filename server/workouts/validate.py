#!/usr/bin/env python3
"""
PreToolUse hook — validates proposed workouts against the active training plan.

Called by Claude Code before create_running_workout or create_strength_workout.
Reads the plan file, computes the current phase and week,
and blocks the tool call if the workout violates phase-appropriate constraints.

Exit codes:
  0  — approved, proceed
  2  — blocked; stdout is shown to the model as the reason

Stdin: JSON with keys tool_name and tool_input (Claude Code hook contract).
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

_profile  = os.environ.get("GARMIN_COACH_PROFILE", "").strip()
# Standalone hook (can't import server/utils): inline the same guard as get_profile_suffix —
# an optional manifest field left blank can arrive as the literal '${user_config.profile_name}'.
if _profile.startswith("${"):
    _profile = ""
_suffix   = f"-{_profile}" if _profile else ""
PLAN_FILE = Path.home() / f".garmin-coach{_suffix}-plan.json"

# ─── Phase rules ──────────────────────────────────────────────────────────────
PHASE_MAX_LONG_RUN_KM = {
    "Base":        14,
    "Development": 18,
    "Specific":    21,
    "Taper":       12,
    "Race Week":    6,
}

PHASE_BLOCKED_TYPES = {
    "Base":        {"interval", "tempo"},
    "Development": set(),
    "Specific":    set(),
    "Taper":       {"interval"},
    "Race Week":   {"interval", "tempo"},
}

def get_plan_phase(current_week: int, total_weeks: int) -> str:
    if total_weeks <= 0 or current_week <= 0:
        return "Unknown"
    pct = current_week / total_weeks
    if pct <= 0.25:
        return "Base"
    elif pct <= 0.55:
        return "Development"
    elif pct <= 0.80:
        return "Specific"
    elif pct <= 0.92:
        return "Taper"
    else:
        return "Race Week"


def load_plan() -> dict | None:
    if not PLAN_FILE.exists():
        return None
    try:
        return json.loads(PLAN_FILE.read_text())
    except Exception:
        return None


def total_distance_km(steps: list[dict]) -> float:
    """Recursively sum distance_meters across all steps (including nested repeats)."""
    total = 0.0
    for step in steps:
        if step.get("type") == "repeat":
            inner = step.get("steps", [])
            iters = step.get("iterations", 1)
            total += total_distance_km(inner) * iters
        elif "distance_meters" in step:
            total += step["distance_meters"] / 1000.0
    return total


def _collect_step_types(steps: list[dict]) -> set[str]:
    """Recursively collect all step type strings (including inside repeat blocks)."""
    types = set()
    for step in steps:
        if step.get("type") == "repeat":
            types |= _collect_step_types(step.get("steps", []))
        else:
            types.add(step.get("type", ""))
    return types


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # malformed input — fail open

    tool_name  = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    plan = load_plan()
    if not plan:
        sys.exit(0)  # no plan → nothing to validate against

    # ── Compute current week and phase ───────────────────────────────────────
    today         = date.today()
    created_on    = date.fromisoformat(plan["created_on"])
    total_weeks   = plan["total_weeks"]
    weeks_elapsed = (today - created_on).days // 7
    current_week  = min(weeks_elapsed + 1, total_weeks)
    phase         = get_plan_phase(current_week, total_weeks)
    race_name     = plan.get("race_name", "race")
    race_date     = plan.get("race_date", "")

    violations: list[str] = []

    # ── Running workout checks ────────────────────────────────────────────────
    if tool_name == "create_running_workout":
        steps       = tool_input.get("steps", [])
        workout_name = tool_input.get("workout_name", "").lower()
        distance_km  = total_distance_km(steps)

        # 1. Phase distance cap
        phase_cap = PHASE_MAX_LONG_RUN_KM.get(phase, 99)
        if distance_km > phase_cap:
            violations.append(
                f"Distance {distance_km:.1f} km exceeds {phase} phase cap of {phase_cap} km."
            )

        # 2. Early-base step ceiling (weeks 1–3: max long run = 8 km)
        if phase == "Base" and current_week <= 3 and distance_km > 8:
            violations.append(
                f"Week {current_week} of Base phase: long runs should stay ≤ 8 km. "
                f"Proposed: {distance_km:.1f} km."
            )

        # 3. Intensity gate — no intervals/tempo in Base (recurses into repeat blocks)
        blocked = PHASE_BLOCKED_TYPES.get(phase, set())
        all_types = _collect_step_types(steps)
        for stype in all_types:
            if stype in blocked:
                violations.append(
                    f"Step type '{stype}' is not appropriate in {phase} phase "
                    f"(week {current_week} of {total_weeks})."
                )

    # ── Strength workout: no checks for now (always allowed in Base) ──────────

    if not violations:
        print(
            f"[coach-hook] APPROVED — Week {current_week}/{total_weeks} {phase} "
            f"| {race_name} {race_date}"
        )
        sys.exit(0)

    # ── Block ─────────────────────────────────────────────────────────────────
    reason_lines = [
        f"COACH REVIEW REQUIRED — workout blocked by training plan guard.",
        f"  Plan:    {race_name} | Week {current_week} of {total_weeks} ({phase} phase)",
        f"  Issues:",
    ]
    for v in violations:
        reason_lines.append(f"    • {v}")
    reason_lines += [
        "",
        "Please consult the running-coach agent, adjust the workout to fit the "
        "current phase, then retry.",
    ]

    print("\n".join(reason_lines))
    sys.exit(2)


if __name__ == "__main__":
    main()
