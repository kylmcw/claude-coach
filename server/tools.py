from mcp import types


def get_tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_morning_metrics",
            description=(
                "Fetch overnight Garmin metrics (HRV, resting HR, sleep, body battery, stress) "
                "and return a daily readiness decision: GREEN / AMBER / RED"
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_training_load",
            description=(
                "Fetch training status and compute ACWR (acute:chronic workload ratio) "
                "from the last 28 days of activities to assess overreach and injury risk"
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_fitness_trend",
            description=(
                "Fetch VO2 Max values sampled weekly over the past 6 weeks "
                "to assess whether fitness is trending up, stable, or declining"
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_recent_activities",
            description=(
                "Fetch the last 7 days of activities with distance, pace, average HR, "
                "and aerobic/anaerobic training effect scores"
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="analyze_run",
            description=(
                "Fetch detailed running dynamics and form analysis for a specific activity. "
                "Returns cadence, stride length, ground contact time, ground contact balance (L/R %), "
                "vertical oscillation, vertical ratio, running power (if available), "
                "per-km splits, and HR zone breakdown. "
                "Includes actionable form recommendations based on the data. "
                "If no activity_id is provided, analyses the most recent running activity."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {
                        "type": "integer",
                        "description": (
                            "Garmin activity ID to analyse. "
                            "If omitted, the most recent running activity is used. "
                            "Activity IDs can be found in get_recent_activities output or Garmin Connect URLs."
                        )
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="create_running_workout",
            description=(
                "Create a structured running workout on Garmin Connect. "
                "Supports warmup, easy, interval (time or distance), recovery, cooldown, and repeat blocks. "
                "Returns the new workout_id which can be used with schedule_workout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workout_name": {
                        "type": "string",
                        "description": "Name shown in Garmin Connect and on the watch"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional notes about the workout"
                    },
                    "steps": {
                        "type": "array",
                        "description": (
                            "Ordered list of steps. Each step is an object with 'type' plus duration/distance "
                            "and optional intensity target. "
                            "Types: warmup, easy, interval, recovery, cooldown, repeat. "
                            "Duration: duration_minutes (time-based) or distance_meters (distance-based). "
                            "Intensity targets (optional, applies to easy/interval steps): "
                            "  target_hr_zone (int 1–5) — HR zone target shown on watch; "
                            "  target_hr_low + target_hr_high (bpm) — custom HR range; "
                            "  target_pace_slow + target_pace_fast (sec/km) — pace band "
                            "    e.g. target_pace_slow=360, target_pace_fast=345 for a 5:45–6:00/km easy run. "
                            "Repeat steps require 'iterations' and a nested 'steps' list. "
                            "Example: [{\"type\":\"warmup\",\"duration_minutes\":10},"
                            "{\"type\":\"repeat\",\"iterations\":5,\"steps\":["
                            "{\"type\":\"interval\",\"distance_meters\":400,"
                            "\"target_pace_slow\":270,\"target_pace_fast\":255},"
                            "{\"type\":\"recovery\",\"duration_minutes\":2}]},"
                            "{\"type\":\"cooldown\",\"duration_minutes\":10}]"
                        ),
                        "items": {"type": "object"}
                    }
                },
                "required": ["workout_name", "steps"]
            }
        ),
        types.Tool(
            name="create_strength_workout",
            description=(
                "Create a strength/gym workout on Garmin Connect. "
                "Each exercise becomes a set of work+rest steps on the watch. "
                "Exercises can be rep-based (reps) or time-based (duration_seconds) "
                "for holds, planks, carries, etc. "
                "Returns the new workout_id which can be used with schedule_workout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workout_name": {
                        "type": "string",
                        "description": "Name shown in Garmin Connect and on the watch"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional notes (e.g. 'Leg day – heavy')"
                    },
                    "exercises": {
                        "type": "array",
                        "description": (
                            "List of exercises. Each has: name (string), sets (int), "
                            "rest_seconds (int), and either reps (int) for rep-based "
                            "or duration_seconds (int) for time-based (holds, planks, carries). "
                            "One of reps or duration_seconds is required per exercise. "
                            "Examples: "
                            "[{\"name\":\"Squat\",\"sets\":4,\"reps\":6,\"rest_seconds\":120}, "
                            "{\"name\":\"Plank\",\"sets\":3,\"duration_seconds\":45,\"rest_seconds\":60}]"
                        ),
                        "items": {"type": "object"}
                    }
                },
                "required": ["workout_name", "exercises"]
            }
        ),
        types.Tool(
            name="schedule_workout",
            description=(
                "Schedule an existing Garmin workout to a specific calendar date. "
                "Use after create_running_workout or create_strength_workout to assign it to a day."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workout_id": {
                        "type": "integer",
                        "description": "The workout ID returned by a create_* tool"
                    },
                    "date": {
                        "type": "string",
                        "description": "Target date in YYYY-MM-DD format"
                    }
                },
                "required": ["workout_id", "date"]
            }
        ),
        types.Tool(
            name="get_scheduled_workout",
            description=(
                "Read the workout(s) scheduled on the Garmin Connect calendar for a given date. "
                "Returns the workout name, sport type, and the full step/exercise structure with target "
                "paces, HR zones, durations, distances, sets and reps where defined. "
                "If no date is given, defaults to tomorrow in the user's local timezone. "
                "If nothing is scheduled, returns 'No workout scheduled for <date>'. "
                "Lists all entries when multiple workouts are scheduled on the same date."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": (
                            "Optional target date in YYYY-MM-DD format. "
                            "Defaults to tomorrow in the user's local timezone."
                        )
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_future_schedule",
            description=(
                "Fetch scheduled workouts for the next N days starting from today. "
                "Returns a day-by-day list showing what is already on the Garmin Connect calendar, "
                "including workouts that were manually rescheduled from other days. "
                "Useful for planning context — call this to see the full upcoming week before "
                "deciding whether to create or schedule new workouts. "
                "Defaults to 7 days if no argument is given. Maximum 90 days."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look ahead, starting from today. Defaults to 7. Maximum 90.",
                        "default": 7
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="calibrate",
            description=(
                "Compute personal HRV and resting HR baselines from the last 30 days of Garmin data "
                "and save them to ~/.garmin-coach.json. "
                "Runs automatically on first use and silently every 7 days thereafter. "
                "Call this manually after a significant fitness change or to force a refresh."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_zones",
            description=(
                "Fetch lactate threshold data from Garmin and derive personalised HR and pace zones. "
                "Returns easy, aerobic, threshold, and interval zones with bpm ranges and pace ranges (sec/km). "
                "Zones are derived from LTHR using Friel running coefficients. "
                "Use this to get the actual easy HR ceiling, threshold pace, or any zone boundary "
                "rather than relying on generic heuristics."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_weekly_review",
            description=(
                "Review the current week's training vs last week. "
                "Compares total distance, time, session count, and activity breakdown. "
                "Highlights best session, reports ACWR, and gives a coming-week recommendation."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_monthly_review",
            description=(
                "Review the current calendar month's training vs last month. "
                "Covers total distance, time, sessions, weekly consistency score, "
                "longest run, and VO2 Max movement over the period. "
                "Ends with a focus area for the coming month."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_run_window",
            description=(
                "Check today's weather and recommend the best time window to run. "
                "On weekdays evaluates morning (6–9am) and lunch (12–1pm) slots. "
                "On weekends evaluates all daylight windows (morning through evening). "
                "Each window is scored 0–100 based on precipitation probability, weather code, "
                "feels-like temperature, wind speed, and UV index. "
                "If no location is provided, auto-detects current location via IP geolocation. "
                "Accepts an optional location name (e.g. 'Mallusk', 'Belfast', 'London') "
                "to check weather for a specific place instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "Optional place name to get weather for (e.g. 'Mallusk', 'Belfast'). "
                            "If omitted, your current location is auto-detected via IP geolocation."
                        )
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="setup_training_plan",
            description=(
                "Set up or update the overarching half-marathon training plan. "
                "When called with no arguments, fetches your current Garmin race predictions "
                "and returns a questionnaire to complete. "
                "When called with race_date and training_days_per_week (at minimum), "
                "creates and saves the plan. "
                "Once a plan exists it is automatically referenced in all daily coaching tools."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "race_name": {
                        "type": "string",
                        "description": "Name of the A race (e.g. 'Belfast Half Marathon')"
                    },
                    "race_date": {
                        "type": "string",
                        "description": "Race date in YYYY-MM-DD format"
                    },
                    "race_distance": {
                        "type": "string",
                        "enum": ["5k", "10k", "half_marathon", "marathon"],
                        "description": "Race distance (default: half_marathon)"
                    },
                    "b_race_name": {
                        "type": "string",
                        "description": "Optional B race name (tune-up / fitness check)"
                    },
                    "b_race_date": {
                        "type": "string",
                        "description": "Optional B race date in YYYY-MM-DD format"
                    },
                    "training_days_per_week": {
                        "type": "integer",
                        "minimum": 3,
                        "maximum": 7,
                        "description": "How many days per week available to train (running + strength combined)"
                    },
                    "include_strength": {
                        "type": "boolean",
                        "description": "Whether to incorporate strength/gym sessions (recommended)"
                    },
                    "strength_days_per_week": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3,
                        "description": "Strength sessions per week (only used if include_strength=true)"
                    },
                    "target_time": {
                        "type": "string",
                        "description": "Goal finish time as H:MM:SS (e.g. '1:50:00'). If omitted, uses Garmin race prediction."
                    },
                    "blocked_days": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Days of the week the user cannot train, e.g. ['Monday', 'Wednesday']. Empty list or omit if no blocked days."
                    },
                    "planned_weekly_km": {
                        "type": "number",
                        "description": "Target weekly running distance in km. Used alongside volume_change to recommend training methodology."
                    },
                    "volume_change": {
                        "type": "string",
                        "enum": ["continue", "increase", "decrease", "starting_fresh"],
                        "description": "Whether the user plans to continue at current volume, increase, decrease, or is starting fresh from little/no running."
                    },
                    "methodology": {
                        "type": "string",
                        "enum": ["polarized", "pyramidal"],
                        "description": "Override the coach-recommended methodology. If omitted, the coach selects based on run days and planned volume."
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_plan_status",
            description=(
                "Show the full training plan status: race details, current week and phase, "
                "predicted vs target time, training structure, and a week-by-week phase breakdown. "
                "Returns an error if no plan has been set up yet."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="clear_training_plan",
            description=(
                "Delete the current training plan. "
                "Use when starting a new training cycle or to reset the plan entirely. "
                "Requires confirmation=true to prevent accidental deletion."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "confirmation": {
                        "type": "boolean",
                        "description": "Must be true to confirm deletion"
                    }
                },
                "required": ["confirmation"]
            }
        ),
        types.Tool(
            name="delete_workout",
            description=(
                "Permanently delete a workout from the Garmin Connect workout library. "
                "This removes the workout template entirely — it cannot be undone. "
                "To remove a workout from the calendar without deleting it, use unschedule_workout instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workout_id": {
                        "type": "integer",
                        "description": "The workout ID to delete (returned by create_running_workout or create_strength_workout)"
                    }
                },
                "required": ["workout_id"]
            }
        ),
        types.Tool(
            name="unschedule_workout",
            description=(
                "Remove a scheduled workout from the Garmin Connect calendar without deleting the workout template. "
                "The workout remains in the library and can be rescheduled later. "
                "Use the scheduled_workout_id (not the workout_id) — this is the calendar-specific ID "
                "returned by get_scheduled_workout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scheduled_workout_id": {
                        "type": "integer",
                        "description": "The scheduled workout ID (calendar entry ID, not the workout template ID)"
                    }
                },
                "required": ["scheduled_workout_id"]
            }
        ),
        types.Tool(
            name="log_workout_feedback",
            description=(
                "Log a completed workout and optional post-run feedback to the coaching history database. "
                "Records date, type, distance, pace, and HR alongside RPE (1–10), subjective feel, "
                "any niggles, and free-text notes. Automatically links to the current training cycle "
                "so history spans multiple races. Use after every completed session to build "
                "longitudinal coaching context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "date":                 {"type": "string",  "description": "Date in YYYY-MM-DD format. Defaults to today."},
                    "activity_id":          {"type": "integer", "description": "Garmin activity ID, if known."},
                    "garmin_workout_id":    {"type": "integer", "description": "Garmin workout template ID, if known."},
                    "type":                 {"type": "string",  "description": "Workout type: easy_run, long_run, tempo, interval, strength, race, cross_training, rest"},
                    "planned_distance_km":  {"type": "number",  "description": "Planned distance in km."},
                    "actual_distance_km":   {"type": "number",  "description": "Actual distance completed in km."},
                    "avg_pace":             {"type": "string",  "description": "Average pace, e.g. '6:45'."},
                    "avg_hr":               {"type": "integer", "description": "Average heart rate in bpm."},
                    "completed":            {"type": "boolean", "description": "Whether the full workout was completed. Defaults to true."},
                    "rpe":                  {"type": "integer", "description": "Rate of perceived exertion, 1 (trivial) – 10 (maximal)."},
                    "feel":                 {"type": "string",  "description": "Subjective feel: great, good, okay, tough, bad."},
                    "niggles":              {"type": "string",  "description": "Any pain, tightness, or discomfort worth noting."},
                    "notes":                {"type": "string",  "description": "Free-text coaching notes."},
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_workout_history",
            description=(
                "Retrieve logged workout history with feedback from the coaching database. "
                "Returns workouts ordered by date descending, including RPE, feel, niggles, and notes. "
                "The running coach uses this to identify fatigue patterns, load trends, recurring niggles, "
                "and form issues across weeks or entire training cycles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of workouts to return. Defaults to 20."
                    },
                    "current_cycle_only": {
                        "type": "boolean",
                        "description": (
                            "If true (default), scope results to the current training cycle. "
                            "Set false to retrieve history across all training cycles."
                        )
                    },
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_pending_suggestions",
            description=(
                "Return all pending coaching suggestions that have not yet been approved or denied. "
                "Suggestions are generated automatically during the weekly review based on ACWR, "
                "volume trends, and session consistency. Each suggestion has an id, recommendation, "
                "rationale, and action_type. Use approve_suggestion to act on them."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="set_exercise_defaults",
            description=(
                "Set or update default weights, sets, and reps for one or more strength exercises. "
                "Stored in the coaching database and automatically applied when creating strength "
                "workouts — no need to specify weight each time. Each exercise variation is tracked "
                "separately (e.g. 'Bench Press (Barbell)' vs 'Bench Press (Dumbbell)'). "
                "Custom exercises not in the Garmin library are supported; supply garmin_category "
                "and garmin_exercise_name to ensure they appear structured on the watch."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "exercises": {
                        "type": "array",
                        "description": "List of exercises to set defaults for.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":                 {"type": "string",  "description": "Exercise name, including variation (e.g. 'Squat (Barbell)', 'Bench Press (Dumbbell)')"},
                                "weight_kg":            {"type": "number",  "description": "Working weight in kg"},
                                "sets":                 {"type": "integer", "description": "Default number of sets"},
                                "reps":                 {"type": "integer", "description": "Default reps per set"},
                                "garmin_category":      {"type": "string",  "description": "Garmin exercise category (e.g. 'CHEST_PRESS'). Auto-resolved if omitted."},
                                "garmin_exercise_name": {"type": "string",  "description": "Garmin exercise name (e.g. 'BARBELL_BENCH_PRESS'). Auto-resolved if omitted."},
                                "notes":                {"type": "string",  "description": "Optional notes (e.g. 'pause at bottom', 'RPE 8 cap')"}
                            },
                            "required": ["name"]
                        }
                    }
                },
                "required": ["exercises"]
            }
        ),
        types.Tool(
            name="log_strength_progress",
            description=(
                "Log the outcome of a strength session per exercise and get progressive overload "
                "suggestions. Completed exercises (all reps hit) → +10% weight rounded to nearest "
                "2.5 kg. Failed exercises → -10% weight. Suggestions are returned for review; "
                "set apply_changes=true to immediately update the stored defaults for next session."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "exercises": {
                        "type": "array",
                        "description": "Per-exercise outcome from this session.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":      {"type": "string",  "description": "Exercise name (must match an existing default)"},
                                "completed": {"type": "boolean", "description": "True if all reps were completed, false if failed"},
                                "notes":     {"type": "string",  "description": "Optional session notes for this exercise"}
                            },
                            "required": ["name", "completed"]
                        }
                    },
                    "apply_changes": {
                        "type": "boolean",
                        "description": "If true (default), update stored defaults immediately. If false, return suggestions only."
                    }
                },
                "required": ["exercises"]
            }
        ),
        types.Tool(
            name="set_exercise_override",
            description=(
                "Temporarily adjust the PROGRAMMED weight for an exercise without changing its "
                "stored base default. Use for a sick/deload week, a travel-gym week, or any period "
                "you want lighter (or heavier) weights that auto-revert afterwards. "
                "kind='pct' applies a signed percentage (e.g. value -20 = 20% lighter); "
                "kind='delta' applies a signed kg change (e.g. value -10 = 10 kg lighter). "
                "Optional start_date/end_date (YYYY-MM-DD) scope the window — outside it the "
                "exercise reverts to its base weight automatically. Overrides do not stack: the "
                "newest active override for an exercise wins. Tag with a label (e.g. 'sick') so you "
                "can clear a whole batch at once."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name":       {"type": "string",  "description": "Exercise name (matches your defaults, e.g. 'Squat')"},
                    "kind":       {"type": "string",  "enum": ["pct", "delta"], "description": "'pct' = percentage adjustment, 'delta' = kg adjustment"},
                    "value":      {"type": "number",  "description": "Signed amount. Negative = lighter (e.g. -20 with pct, -10 with delta)."},
                    "label":      {"type": "string",  "description": "Optional tag grouping related overrides (e.g. 'sick', 'deload', 'travel')"},
                    "start_date": {"type": "string",  "description": "Optional YYYY-MM-DD start of the window. Open-ended if omitted."},
                    "end_date":   {"type": "string",  "description": "Optional YYYY-MM-DD end of the window (inclusive). Open-ended if omitted."}
                },
                "required": ["name", "kind", "value"]
            }
        ),
        types.Tool(
            name="clear_exercise_override",
            description=(
                "Remove active exercise weight overrides, reverting programmed weights to their "
                "base defaults. Specify exactly one of: name (clear overrides for one exercise), "
                "label (clear all overrides with that tag, e.g. 'sick'), or all=true (clear every "
                "active override). Overrides are deactivated, not deleted, so history is preserved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name":  {"type": "string",  "description": "Clear active overrides for this exercise."},
                    "label": {"type": "string",  "description": "Clear all active overrides carrying this label."},
                    "all":   {"type": "boolean", "description": "If true, clear every active override."}
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_daily_briefing",
            description=(
                "Single-call morning briefing: readiness (HRV, sleep, Training Readiness score → "
                "GREEN/AMBER/RED), today's scheduled Garmin workout, best weather window, and a "
                "unified go/modify/rest verdict. When AMBER/RED meets a quality session, "
                "automatically proposes a swap and logs it as a coaching suggestion. "
                "Accepts an optional location override for weather."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Optional location name for weather (e.g. 'Belfast'). Auto-detects if omitted."
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_recovery_trend",
            description=(
                "Fetch a 7–14 day day-by-day trend of HRV, resting HR, sleep score, body battery, "
                "and stress. Computes slope-based trend direction per metric and flags overreaching "
                "or illness signals (HRV falling + RHR rising, sustained low HRV, chronically "
                "depleted body battery). Anchors HRV analysis on Garmin's own HRV status "
                "(balanced/unbalanced/low) where available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look back. Default 14, minimum 7, maximum 28.",
                        "default": 14
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="generate_week",
            description=(
                "Generate and schedule a full week of training sessions on the Garmin Connect calendar "
                "based on the active training plan phase, ACWR, and blocked days. "
                "Creates running workout templates with personalized pace/HR targets from your "
                "lactate threshold data, schedules them, and inserts planned rows into the history DB. "
                "Skips sessions already scheduled on a given day. "
                "Requires an active training plan (setup_training_plan first)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_km": {
                        "type": "number",
                        "description": "Total running volume target for the week in km. If omitted, uses last week's volume as baseline."
                    },
                    "week_offset": {
                        "type": "integer",
                        "description": "0 = this week (default), 1 = next week.",
                        "default": 0
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="mark_week_planned",
            description=(
                "Mark a week as fully planned in the DB so the Monday morning coach skips "
                "workout creation for that week. Called automatically by generate_week; "
                "also call manually after setting up a custom or deload week."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "week_start": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) of the Monday that starts the week. Defaults to this week's Monday if omitted."
                    },
                    "planned_by": {
                        "type": "string",
                        "description": "Label for who/what planned the week (e.g. 'generate_week', 'manual'). Defaults to 'manual'."
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_week_planned",
            description=(
                "Check whether a week has been marked as planned in the DB. "
                "Returns planned (bool), week_start, planned_at, and planned_by."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "week_start": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) of the Monday that starts the week. Defaults to this week's Monday if omitted."
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_race_strategy",
            description=(
                "Generate a race-day pacing strategy from the active training plan. "
                "Includes: goal-vs-prediction gap analysis, per-km target pace bands "
                "(conservative start, goal pace mid-race, negative-split finish), "
                "half-split targets, taper adherence check, and race-day cues. "
                "Accepts an optional target_time override without altering the plan."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_time": {
                        "type": "string",
                        "description": "Optional goal time override in H:MM:SS (e.g. '1:50:00'). Uses plan target if omitted."
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_fitness_scores",
            description=(
                "Fetch Garmin's Endurance Score and Hill Score for today. "
                "Endurance Score tracks sustained aerobic capacity across training history. "
                "Hill Score measures ability to handle elevation. Both are shown alongside "
                "their descriptive levels where available."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="approve_suggestion",
            description=(
                "Approve or deny a pending coaching suggestion by its id. "
                "Approved suggestions are logged as accepted coaching decisions. "
                "Denied suggestions are dismissed. Both outcomes are recorded for future review."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "suggestion_id": {
                        "type": "integer",
                        "description": "The id of the suggestion to review (from get_pending_suggestions)"
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["approve", "deny"],
                        "description": "Whether to approve or deny the suggestion"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes on why you approved or denied"
                    }
                },
                "required": ["suggestion_id", "decision"]
            }
        ),
        types.Tool(
            name="get_exercise_defaults",
            description=(
                "View current default weights, sets, and reps for all tracked strength exercises "
                "(or a single named exercise). Shows the Garmin exercise mapping alongside each entry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional — filter to a specific exercise name."
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="review_strength_workout",
            description=(
                "Pull a completed strength/gym activity from Garmin Connect and compare each "
                "exercise against your stored defaults (weight, sets, reps). "
                "For fully-completed exercises, shows the weight you actually used and whether "
                "it differs from your stored default. "
                "Set apply_defaults=true to save the logged weights as new defaults for all "
                "fully-completed exercises — both updating existing entries and adding new ones. "
                "If no activity_id is provided, uses the most recent fitness_equipment activity "
                "in the last 30 days."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {
                        "type": "integer",
                        "description": "Garmin activity ID to review. Omit to use the most recent strength activity."
                    },
                    "apply_defaults": {
                        "type": "boolean",
                        "description": (
                            "If true, save logged weights as new defaults for all fully-completed exercises. "
                            "Defaults to false (review only)."
                        )
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="backfill_runs",
            description=(
                "Backfill recently completed Garmin activities into the local history DB. "
                "Unlike the morning auto-log (which only reconciles yesterday), this scans the "
                "last N days and logs any activity not already recorded — use it to capture "
                "runs missed because the morning check wasn't run that day. Idempotent: "
                "already-logged activities are skipped."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How many days back to scan (default 14, max 90)."
                    }
                },
                "required": []
            }
        ),
    ]
