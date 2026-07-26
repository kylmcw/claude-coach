import asyncio
import json
from datetime import date, timedelta

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from garmin.calibration import (calibrate_baselines,
                                CALIBRATION_FILE, CALIBRATION_LOOKBACK, RECALIBRATE_AFTER_DAYS)
from garmin.readiness import fetch_morning_data, assess_readiness, assess_training_state
from garmin.training import (fetch_training_load, fetch_fitness_trend, fetch_recent_activities,
                              fetch_weekly_summary, fetch_monthly_summary,
                              fetch_garmin_race_predictions, format_weekly_review, format_monthly_review,
                              generate_week_suggestions, fetch_fitness_scores,
                              fetch_strength_exercise_sets)
from garmin.schedule import (fetch_scheduled_workout, fetch_future_schedule,
                              _format_scheduled_workout, _format_future_schedule)
from garmin.analysis import fetch_run_analysis, format_run_analysis, build_week_context
from garmin.client import get_client
from db.history import (log_workout_to_history, fetch_workout_history, auto_log_missed_workouts,
                        backfill_runs, log_coach_suggestion, fetch_pending_suggestions,
                        review_suggestion, generate_week_plan, mark_week_planned, is_week_planned)
from db.exercises import (set_exercise_defaults, get_exercise_defaults, log_strength_progress,
                           lookup_garmin_exercise, log_exercise_completions, check_progression_due,
                           set_exercise_override, clear_exercise_override)
from coaching.plan import (load_plan, save_plan, format_plan_context, _NO_PLAN_NUDGE, PLAN_FILE,
                            build_setup_questionnaire, create_plan_from_args, format_plan_status)
from coaching.weather import resolve_location, fetch_weather_windows, find_best_run_window
from coaching.briefing import get_daily_briefing
from coaching.recovery_trend import fetch_recovery_trend, analyze_recovery_trend, format_recovery_trend
from coaching.race_strategy import build_race_strategy
from coaching.thresholds import get_zones, fmt_pace
from workouts.workouts import (create_and_upload_running_workout, create_and_upload_strength_workout)
from tools import get_tool_definitions

app = Server("garmin-coach")


def _week_start_arg(arguments: dict | None) -> str:
    """The week_start arg, or this week's Monday (ISO date)."""
    raw = (arguments or {}).get("week_start")
    if raw:
        return raw
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


# ─── MCP tool registry ────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return get_tool_definitions()


# ─── MCP tool dispatcher ──────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict):

    if name == "calibrate":
        hrv_low, hrv_high, rhr_norm = calibrate_baselines()
        try:
            state = json.loads(CALIBRATION_FILE.read_text())
        except Exception:
            state = {}

        hrv_samples = state.get("hrv_samples", 0)
        rhr_samples = state.get("rhr_samples", 0)
        hrv_mean    = state.get("hrv_mean")
        rhr_mean    = state.get("rhr_mean")

        hrv_note = (
            f"{hrv_samples} days of data — using defaults (not enough history)"
            if hrv_samples < 7
            else f"{hrv_samples} days of data"
        )
        rhr_note = (
            f"{rhr_samples} days of data — using defaults (not enough history)"
            if rhr_samples < 7
            else f"{rhr_samples} days of data"
        )

        lines = [
            f"CALIBRATION COMPLETE — {date.today().strftime('%A %d %B %Y')}",
            f"Lookback: {CALIBRATION_LOOKBACK} days  |  Next recalibration: in {RECALIBRATE_AFTER_DAYS} days\n",
            f"HRV baseline:  {hrv_low}–{hrv_high}  (mean {hrv_mean}, {hrv_note})",
            f"RHR baseline:  {rhr_norm} bpm          (mean {rhr_mean}, {rhr_note})",
            f"\nSaved to {CALIBRATION_FILE}",
        ]

        if hrv_samples < 7:
            lines.append(
                "\n⚠  Less than 7 days of HRV data found. "
                "Population defaults are in use — accuracy will improve as more data accumulates."
            )

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_morning_metrics":
        auto_logged = auto_log_missed_workouts()

        data = fetch_morning_data()
        status, flags, decision = assess_readiness(data)

        bb_str     = f"{data['body_battery']}/100" if data['body_battery']     is not None else "N/A"
        stress_str = f"{data['avg_stress']}/100"   if data['avg_stress']       is not None else "N/A"
        tr_str     = f"{data['tr_score']}/100"     if data.get('tr_score')     is not None else "N/A"
        hrv_status_tag = f"  [{data['hrv_status']}]" if data.get('hrv_status') else ""

        # Sleep display: score if available, else hours + stages
        if data.get('sleep_score') is not None:
            sleep_line = f"Sleep Score:  {data['sleep_score']}/100"
            if data.get('sleep_hours'):
                sleep_line += f"  ({data['sleep_hours']}h total"
                parts = []
                if data.get('deep_hours'):  parts.append(f"Deep {data['deep_hours']}h")
                if data.get('rem_hours'):   parts.append(f"REM {data['rem_hours']}h")
                if parts:
                    sleep_line += f", {', '.join(parts)})"
                else:
                    sleep_line += ")"
        else:
            sleep_line = f"Sleep:        {data['sleep_hours'] or 'N/A'}h"
            parts = []
            if data.get('deep_hours'):  parts.append(f"Deep {data['deep_hours']}h")
            if data.get('rem_hours'):   parts.append(f"REM {data['rem_hours']}h")
            if parts:
                sleep_line += f"  ({', '.join(parts)})"

        summary = (
            f"DATE: {date.today().strftime('%A %d %B %Y')}\n\n"
            f"READINESS METRICS:\n"
            f"  HRV:              {data['hrv'] or 'N/A'}{hrv_status_tag}\n"
            f"  Resting HR:       {data['resting_hr'] or 'N/A'} bpm\n"
            f"  {sleep_line}\n"
            f"  Body Battery:     {bb_str}\n"
            f"  Avg Stress:       {stress_str}\n"
            f"  Training Ready:   {tr_str}\n"
        )

        if data.get("stale_warnings"):
            summary += "\nDATA WARNINGS:\n"
            for w in data["stale_warnings"]:
                summary += f"  ⚠ {w}\n"

        summary += (
            f"\nFLAGS:\n" + "\n".join(f"  - {f}" for f in flags) + "\n\n"
            f"STATUS: {status.upper()}\n\n"
            f"TODAY: {decision}"
        )
        plan = load_plan()
        summary += format_plan_context(plan) if plan else _NO_PLAN_NUDGE
        if auto_logged:
            summary += "\n\n📋 HISTORY: " + " | ".join(auto_logged)
        return [types.TextContent(type="text", text=summary)]

    elif name == "get_training_load":
        data = fetch_training_load()
        acwr_assessment = assess_training_state(data)

        from garmin.training import _fmt_load_focus
        load_focus_str = _fmt_load_focus(data.get("load_focus")) or "N/A"
        summary = (
            f"TRAINING LOAD — {date.today().strftime('%A %d %B %Y')}\n\n"
            f"  Training Status:        {data['training_status'] or 'N/A'}\n"
            f"  Garmin Load (7d):       {data['training_load_7d'] or 'N/A'}\n"
            f"  Acute Load (7d):        {data['acute_load']}\n"
            f"  Chronic Load (4wk avg): {data['chronic_load']}\n"
            f"  ACWR:                   {data['acwr'] or 'N/A'}  (reference only — training status governs, do NOT judge against 0.8–1.3)\n"
            f"  Load Focus:             {load_focus_str}\n"
            f"  Sessions (last 7d):     {data['sessions_last_7d']}\n"
            f"  Sessions (last 28d):    {data['sessions_last_28d']}\n\n"
            f"ASSESSMENT: {acwr_assessment}"
        )
        plan = load_plan()
        summary += format_plan_context(plan) if plan else _NO_PLAN_NUDGE
        return [types.TextContent(type="text", text=summary)]

    elif name == "get_fitness_trend":
        trend = fetch_fitness_trend()
        lines = [f"VO2 MAX TREND — {date.today().strftime('%A %d %B %Y')}\n"]

        for entry in trend:
            vo2_str = str(entry["vo2max"]) if entry["vo2max"] is not None else "N/A"
            lines.append(f"  {entry['date']}  →  {vo2_str}")

        valid = [e["vo2max"] for e in trend if e["vo2max"] is not None]
        if len(valid) >= 2:
            delta = round(valid[-1] - valid[0], 1)
            if delta > 0.5:
                direction = f"Trending UP (+{delta}) — adaptation is happening."
            elif delta < -0.5:
                direction = f"Trending DOWN ({delta}) — check training stress and recovery."
            else:
                direction = "Stable — maintaining current fitness level."
            lines.append(f"\nTREND: {direction}")
        else:
            lines.append("\nTREND: Not enough data points to determine direction.")

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_recent_activities":
        activities = fetch_recent_activities()

        if not activities:
            return [types.TextContent(type="text", text="No activities found in the last 7 days.")]

        lines = [f"RECENT ACTIVITIES — last 7 days\n"]
        for act in activities:
            aerobic_te   = act['aerobic_te']   if act['aerobic_te']   is not None else "N/A"
            anaerobic_te = act['anaerobic_te'] if act['anaerobic_te'] is not None else "N/A"
            lines.append(
                f"  {act['date']}  {act['name']} ({act['type']})\n"
                f"    Distance: {act['distance_km']} km   Duration: {act['duration_min']} min\n"
                f"    Avg HR:   {act['avg_hr'] or 'N/A'} bpm   Pace: {act['avg_pace']}\n"
                f"    Training Effect — Aerobic: {aerobic_te}   Anaerobic: {anaerobic_te}\n"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "analyze_run":
        activity_id = arguments.get("activity_id")
        data = fetch_run_analysis(activity_id)
        if data.get("error"):
            return [types.TextContent(type="text", text=f"ANALYZE RUN ERROR — {data['error']}")]
        plan = load_plan()
        ctx = format_plan_context(plan) if plan else _NO_PLAN_NUDGE
        # Give the single-run analysis visibility into the rest of the week so it
        # doesn't prescribe quality that's already been done.
        try:
            week_ctx = build_week_context(fetch_recent_activities(7), fetch_training_load())
        except Exception:
            week_ctx = ""
        return [types.TextContent(type="text", text=format_run_analysis(data, ctx, week_ctx))]

    elif name == "create_running_workout":
        workout_name = arguments["workout_name"]
        description  = arguments.get("description")
        steps_raw    = arguments["steps"]

        result     = create_and_upload_running_workout(workout_name, description, steps_raw)
        workout_id = result["workout_id"]
        step_count = result["step_count"]

        summary = (
            f"RUNNING WORKOUT CREATED\n"
            f"  Name:       {workout_name}\n"
            f"  Workout ID: {workout_id}\n"
            f"  Steps:      {step_count}\n"
            f"\nUse schedule_workout with workout_id={workout_id} to add it to your calendar."
        )
        return [types.TextContent(type="text", text=summary)]

    elif name == "create_strength_workout":
        workout_name = arguments["workout_name"]
        description  = arguments.get("description")
        exercises    = arguments["exercises"]

        result     = create_and_upload_strength_workout(workout_name, description, exercises)
        workout_id = result["workout_id"]
        ex_summary = result["exercise_summary"]
        unmapped   = result.get("unmapped", [])

        lines = [
            f"STRENGTH WORKOUT CREATED",
            f"  Name:       {workout_name}",
            f"  Workout ID: {workout_id}",
            f"  Exercises:  {ex_summary}",
            f"",
            f"Use schedule_workout with workout_id={workout_id} to add it to your calendar.",
        ]
        if unmapped:
            lines += [
                f"",
                f"⚠ Unmapped exercises (uploaded without Garmin category): {', '.join(unmapped)}",
                f"Use set_exercise_defaults with garmin_category and garmin_exercise_name to fix.",
            ]
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "schedule_workout":
        workout_id  = int(arguments["workout_id"])
        target_date = arguments["date"]

        client = get_client()
        client.schedule_workout(workout_id, target_date)

        summary = (
            f"WORKOUT SCHEDULED\n"
            f"  Workout ID: {workout_id}\n"
            f"  Date:       {target_date}\n"
            f"\nIt will appear in your Garmin Connect calendar and sync to your watch."
        )
        return [types.TextContent(type="text", text=summary)]

    elif name == "get_scheduled_workout":
        target_date = arguments.get("date") if arguments else None
        result      = fetch_scheduled_workout(target_date)
        summary     = _format_scheduled_workout(result["date"], result["workouts"])
        return [types.TextContent(type="text", text=summary)]

    elif name == "get_future_schedule":
        days    = int((arguments or {}).get("days", 7))
        entries = fetch_future_schedule(days)
        summary = _format_future_schedule(entries)
        return [types.TextContent(type="text", text=summary)]

    elif name == "get_weekly_review":
        this_week = fetch_weekly_summary(0)
        last_week = fetch_weekly_summary(1)
        load_data = fetch_training_load()
        plan = load_plan()
        ctx = format_plan_context(plan) if plan else _NO_PLAN_NUDGE

        # Generate and stage suggestions for the coming week
        suggestions = generate_week_suggestions(this_week, last_week, load_data)
        context_summary = (
            f"ACWR: {load_data.get('acwr')}, "
            f"vol: {this_week.get('total_distance_km')}km, "
            f"sessions: {this_week.get('session_count')}"
        )
        for s in suggestions:
            try:
                log_coach_suggestion(
                    trigger="weekly_review",
                    context_summary=context_summary,
                    recommendation=s["recommendation"],
                    rationale=s["rationale"],
                    action_type=s.get("action_type"),
                )
            except Exception:
                pass

        return [types.TextContent(type="text", text=format_weekly_review(this_week, last_week, load_data, ctx))]

    elif name == "get_monthly_review":
        this_month = fetch_monthly_summary(0)
        last_month = fetch_monthly_summary(1)
        trend      = fetch_fitness_trend()
        plan = load_plan()
        ctx = format_plan_context(plan) if plan else _NO_PLAN_NUDGE
        return [types.TextContent(type="text", text=format_monthly_review(this_month, last_month, trend, ctx))]

    elif name == "get_run_window":
        location_arg = arguments.get("location") or None
        try:
            lat, lon, place_label = resolve_location(location_arg)
        except Exception as exc:
            return [types.TextContent(
                type="text",
                text=f"RUN WINDOW ERROR — could not determine location: {exc}"
            )]

        today      = date.today()
        is_weekday = today.weekday() < 5  # Monday=0 … Sunday=6

        hours  = fetch_weather_windows(lat, lon)
        result = find_best_run_window(hours, is_weekday)

        day_type   = "Weekday" if is_weekday else "Weekend"
        scope_note = (
            "morning and lunch windows evaluated"
            if is_weekday
            else "all daylight windows evaluated"
        )

        lines = [
            f"RUN WINDOW — {today.strftime('%A %d %B %Y')}",
            f"Location: {place_label}",
            f"{day_type} ({scope_note})\n",
        ]

        rec = result["recommended"]
        if rec:
            quality = (
                "Excellent conditions."  if rec["score"] >= 80 else
                "Good conditions."       if rec["score"] >= 60 else
                "Marginal — dress accordingly." if rec["score"] >= 40 else
                "Poor conditions — consider a treadmill or reschedule."
            )
            lines.append(
                f"RECOMMENDED: {rec['window']}  "
                f"(best slot {rec['best_hour']:02d}:00)"
            )
            lines.append(f"  Score:       {rec['score']}/100 — {quality}")
            lines.append(f"  Weather:     {rec['weather_desc'].title()}")
            lines.append(
                f"  Temperature: {rec['temp']:.1f}°C  "
                f"(feels like {rec['feels_like']:.1f}°C)"
            )
            lines.append(f"  Rain chance: {rec['precip_prob']}%")
            lines.append(f"  Wind:        {rec['wind_kmh']:.0f} km/h")
            if rec["notes"]:
                lines.append(f"  Conditions:  {'; '.join(rec['notes'])}")

        if len(result["windows"]) > 1:
            lines.append("\nALL WINDOWS (best→worst):")
            for w in result["windows"]:
                marker = "★" if w is rec else " "
                lines.append(
                    f"  {marker} {w['window']:<25} "
                    f"Score {w['score']:>3}/100  "
                    f"{w['weather_desc']}, "
                    f"{w['feels_like']:.0f}°C feels-like, "
                    f"{w['precip_prob']}% rain"
                )

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "setup_training_plan":
        race_date_str = arguments.get("race_date")
        training_days = arguments.get("training_days_per_week")
        preds = fetch_garmin_race_predictions()
        if not race_date_str or not training_days:
            return [types.TextContent(type="text", text=build_setup_questionnaire(preds))]
        plan, confirmation = create_plan_from_args(arguments, preds)
        save_plan(plan)
        return [types.TextContent(type="text", text=confirmation)]

    elif name == "get_plan_status":
        plan = load_plan()
        if not plan:
            return [types.TextContent(
                type="text",
                text=(
                    "NO TRAINING PLAN SET UP.\n\n"
                    "Call setup_training_plan (with no arguments) to start the setup flow. "
                    "The coach will pull your current Garmin race predictions and walk you through the questions."
                )
            )]
        return [types.TextContent(type="text", text=format_plan_status(plan))]

    elif name == "clear_training_plan":
        if not arguments.get("confirmation"):
            return [types.TextContent(
                type="text",
                text="Plan NOT deleted. Pass confirmation=true to confirm deletion."
            )]
        if PLAN_FILE.exists():
            PLAN_FILE.unlink()
            return [types.TextContent(type="text", text=f"Training plan deleted ({PLAN_FILE}).")]
        return [types.TextContent(type="text", text="No plan file found — nothing to delete.")]

    elif name == "delete_workout":
        workout_id = int(arguments["workout_id"])
        client = get_client()
        client.delete_workout(workout_id)

        summary = (
            f"WORKOUT DELETED\n"
            f"  Workout ID: {workout_id}\n"
            f"\nThe workout has been permanently removed from your Garmin Connect library."
        )
        return [types.TextContent(type="text", text=summary)]

    elif name == "unschedule_workout":
        scheduled_workout_id = int(arguments["scheduled_workout_id"])
        client = get_client()
        client.unschedule_workout(scheduled_workout_id)

        summary = (
            f"WORKOUT UNSCHEDULED\n"
            f"  Scheduled workout ID: {scheduled_workout_id}\n"
            f"\nThe workout has been removed from your calendar but remains in your workout library."
        )
        return [types.TextContent(type="text", text=summary)]

    elif name == "log_workout_feedback":
        date_str          = arguments.get("date") or date.today().isoformat()
        activity_id       = arguments.get("activity_id")
        garmin_workout_id = arguments.get("garmin_workout_id")
        workout_type      = arguments.get("type", "unknown")
        planned_km        = arguments.get("planned_distance_km")
        actual_km         = arguments.get("actual_distance_km")
        avg_pace          = arguments.get("avg_pace")
        avg_hr            = arguments.get("avg_hr")
        completed         = arguments.get("completed", True)
        rpe               = arguments.get("rpe")
        feel              = arguments.get("feel")
        niggles           = arguments.get("niggles")
        notes             = arguments.get("notes")

        row_id = log_workout_to_history(
            date_str, activity_id, garmin_workout_id, workout_type,
            planned_km, actual_km, avg_pace, avg_hr, completed,
            rpe, feel, niggles, notes,
        )

        lines = [
            f"WORKOUT LOGGED  (history row #{row_id})",
            f"  Date:     {date_str}",
            f"  Type:     {workout_type}",
        ]
        if actual_km is not None:
            lines.append(f"  Distance: {actual_km} km")
        if rpe is not None:
            lines.append(f"  RPE:      {rpe}/10")
        if feel:
            lines.append(f"  Feel:     {feel}")
        if niggles:
            lines.append(f"  Niggles:  {niggles}")
        if notes:
            lines.append(f"  Notes:    {notes}")

        plan = load_plan()
        if plan:
            lines.append(format_plan_context(plan))

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "backfill_runs":
        days   = max(1, min(int(arguments.get("days", 14)), 90))
        logged = backfill_runs(days=days)
        if logged:
            text = (f"Backfilled {len(logged)} item(s) from the last {days} days:\n  "
                    + "\n  ".join(logged))
        else:
            text = (f"Nothing to backfill from the last {days} days — everything is already "
                    "logged, or no activities were found in that window.")
        return [types.TextContent(type="text", text=text)]

    elif name == "get_workout_history":
        limit              = min(int(arguments.get("limit", 20)), 200)
        current_cycle_only = arguments.get("current_cycle_only", True)

        rows = fetch_workout_history(limit=limit, current_cycle_only=current_cycle_only)

        if not rows:
            return [types.TextContent(
                type="text",
                text=(
                    "No workout history logged yet.\n"
                    "Use log_workout_feedback after each session to build coaching context."
                ),
            )]

        scope = "current cycle only" if current_cycle_only else "all cycles"
        lines = [f"WORKOUT HISTORY  (last {len(rows)} entries — {scope})", "─" * 72]

        for r in rows:
            cycle_label = f"  [{r['race_name']} {r['race_date']}]" if r.get("race_name") else ""
            dist_str    = f"{r['actual_distance_km']}km" if r.get("actual_distance_km") else "—"
            rpe_str     = f"RPE {r['rpe']}" if r.get("rpe") else ""
            lines.append(
                f"{r['date']}  Wk{str(r.get('week_number', '?')):>2}  "
                f"{(r.get('phase') or '?'):<12}  "
                f"{(r.get('type') or '?'):<14}  "
                f"{dist_str:>7}  {rpe_str:<6}"
                f"{cycle_label}"
            )
            detail_parts = []
            if r.get("feel"):
                detail_parts.append(r["feel"])
            if r.get("niggles"):
                detail_parts.append(f"⚠ {r['niggles']}")
            if r.get("feedback_notes"):
                detail_parts.append(r["feedback_notes"])
            if detail_parts:
                lines.append(f"  └─ {' · '.join(detail_parts)}")

        lines.append("─" * 72)
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_pending_suggestions":
        pending = fetch_pending_suggestions()
        if not pending:
            return [types.TextContent(type="text", text="No pending suggestions — run get_weekly_review to generate new ones.")]
        lines = [f"PENDING COACHING SUGGESTIONS ({len(pending)})", ""]
        for s in pending:
            lines += [
                f"[{s['id']}] {s['date']}  ({s['action_type'] or 'note'})",
                f"  {s['recommendation']}",
                f"  WHY: {s['rationale']}",
                "",
            ]
        lines.append("Use approve_suggestion with the id number to approve or deny each one.")
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "approve_suggestion":
        suggestion_id = int(arguments["suggestion_id"])
        decision      = arguments["decision"]
        notes         = arguments.get("notes")
        approved      = decision == "approve"
        updated       = review_suggestion(suggestion_id, approved, notes)
        if not updated:
            return [types.TextContent(type="text", text=f"Suggestion #{suggestion_id} not found or already reviewed.")]
        verb = "Approved" if approved else "Denied"
        msg  = f"{verb} suggestion #{suggestion_id}."
        if notes:
            msg += f" Notes: {notes}"
        return [types.TextContent(type="text", text=msg)]

    elif name == "set_exercise_defaults":
        raw_exercises = arguments.get("exercises", [])
        enriched = []
        for ex in raw_exercises:
            if not ex.get("garmin_category") or not ex.get("garmin_exercise_name"):
                cat, gname = lookup_garmin_exercise(ex["name"])
                ex = dict(ex)
                if cat is not None:
                    ex.setdefault("garmin_category", cat)
                    ex.setdefault("garmin_exercise_name", gname)
            enriched.append(ex)
        results = set_exercise_defaults(enriched)
        lines   = ["EXERCISE DEFAULTS SAVED:"] + [f"  {r}" for r in results]
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "set_exercise_override":
        r = set_exercise_override(
            name=arguments["name"],
            kind=arguments["kind"],
            value=arguments["value"],
            label=arguments.get("label"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
        adj    = f"{r['value']:+g}%" if r["kind"] == "pct" else f"{r['value']:+g}kg"
        window = " to ".join(d for d in (r["start_date"], r["end_date"]) if d) or "open-ended"
        base   = f"{r['base_kg']}kg" if r["base_kg"] is not None else "no base default"
        eff    = f"{r['effective_kg']}kg" if r["effective_kg"] is not None else "—"
        lines  = [
            f"OVERRIDE SET: {r['name']} {adj}" + (f" [{r['label']}]" if r["label"] else ""),
            f"  base {base} → programmed {eff}   (window: {window})",
            "  Base default unchanged — reverts automatically when the window ends or you clear it.",
        ]
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "clear_exercise_override":
        n = clear_exercise_override(
            name=(arguments or {}).get("name"),
            label=(arguments or {}).get("label"),
            clear_all=bool((arguments or {}).get("all")),
        )
        return [types.TextContent(type="text", text=f"Cleared {n} active override(s). Weights revert to base defaults.")]

    elif name == "get_exercise_defaults":
        name_filter = (arguments or {}).get("name")
        rows = get_exercise_defaults(name_filter)
        if not rows:
            msg = "No exercise defaults set yet. Use set_exercise_defaults to get started."
            return [types.TextContent(type="text", text=msg)]
        lines = ["EXERCISE DEFAULTS:"]
        for r in rows:
            weight = f"{r['weight_kg']}kg" if r["weight_kg"] is not None else "no weight"
            scheme = f"{r['sets']}×{r['reps']}" if r["sets"] and r["reps"] else "—"
            garmin = r.get("garmin_exercise_name") or "custom"
            lines.append(f"  {r['name']:<35} {weight:<10} {scheme:<8} [{garmin}]")
            if r.get("notes"):
                lines.append(f"    ↳ {r['notes']}")
        lines.append(f"\n{len(rows)} exercise(s) on file.")
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "log_strength_progress":
        exercises     = arguments.get("exercises", [])
        apply_changes = arguments.get("apply_changes", True)
        result        = log_strength_progress(exercises, apply_changes=apply_changes)
        suggestions   = result["suggestions"]
        applied       = result["applied"]

        lines = ["STRENGTH PROGRESSION REPORT:"]
        held_for_review = 0
        for s in suggestions:
            if s["current_kg"] is None:
                lines.append(f"  ⚠ {s['exercise']}: {s['note']}")
            elif s.get("needs_review"):
                # Failed/off session — default held (ratchet). Log a pending suggestion so the
                # decision to lower is explicit, never silent. Only when actually applying.
                lines.append(f"  ✗ {s['exercise']}: {s['action']}")
                if s.get("notes"):
                    lines.append(f"    ↳ {s['notes']}")
                if applied:
                    held_for_review += 1
                    log_coach_suggestion(
                        trigger="strength_session_failed",
                        context_summary=f"{s['exercise']} failed at {s['current_kg']}kg"
                                        + (f" — {s['notes']}" if s.get("notes") else ""),
                        recommendation=f"Review whether to lower {s['exercise']} below {s['current_kg']}kg",
                        rationale="Ratchet policy: failed/off sessions never auto-lower the stored "
                                  "default (a sick day shouldn't drop base capacity). Lower manually "
                                  "via set_exercise_defaults only if the failure reflects true capacity.",
                        action_type="strength_weight_review",
                    )
            else:
                lines.append(
                    f"  ✓ {s['exercise']}: {s['current_kg']}kg → {s['suggested_kg']}kg  ({s['action']})"
                )
                if s.get("notes"):
                    lines.append(f"    ↳ {s['notes']}")
        if applied:
            lines.append("\nDefaults updated — weights will apply to next workout automatically.")
            if held_for_review:
                lines.append(
                    f"{held_for_review} failed exercise(s) held at current weight and logged for "
                    "review (use get_pending_suggestions). Defaults were not lowered."
                )
        else:
            lines.append("\nSuggestions only — call again with apply_changes=true to save.")
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_daily_briefing":
        location_arg = (arguments or {}).get("location") or None
        text = get_daily_briefing(location_arg)
        return [types.TextContent(type="text", text=text)]

    elif name == "get_recovery_trend":
        days = min(int((arguments or {}).get("days", 14)), 28)
        days = max(days, 7)
        series   = fetch_recovery_trend(days)
        analysis = analyze_recovery_trend(series)
        text     = format_recovery_trend(series, analysis, days)
        return [types.TextContent(type="text", text=text)]

    elif name == "generate_week":
        plan = load_plan()
        if not plan:
            return [types.TextContent(type="text", text=(
                "NO TRAINING PLAN.\n"
                "Call setup_training_plan first, then generate_week."
            ))]

        week_offset = int((arguments or {}).get("week_offset", 0))
        load_data   = fetch_training_load()
        acwr        = load_data.get("acwr")

        # Base target_km on last week's volume if not supplied
        target_km = (arguments or {}).get("target_km")
        if target_km is None:
            this_week = fetch_weekly_summary(0)
            last_week = fetch_weekly_summary(1)
            base_vol  = last_week.get("run_distance_km") or this_week.get("run_distance_km") or 30
            target_km = round(base_vol * 1.05, 1)  # default: +5% on last week
        target_km = float(target_km)

        today      = date.today()
        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)

        created = generate_week_plan(plan, week_start, target_km, load_data)

        if not created:
            return [types.TextContent(type="text", text=(
                f"No new sessions generated for week of {week_start.isoformat()}.\n"
                "Sessions may already exist — use get_future_schedule to review."
            ))]

        lines = [
            f"WEEK GENERATED — w/c {week_start.isoformat()}",
            f"Target volume: {target_km} km  |  Status: {load_data.get('training_status') or 'N/A'} (governs load)",
            f"ACWR: {acwr or 'N/A'} — reference only, not a stop signal (runs high on multi-session days)",
        ]
        try:
            from db.history import assess_recent_feedback
            fb = assess_recent_feedback()
            if fb["backoff"]:
                lines.append(f"⚠ Volume eased for logged feedback: {fb['reason']}.")
        except Exception:
            pass
        lines.append("")
        for s in created:
            if s.get("error"):
                lines.append(f"  {s['date']}  {s['workout_name']} — ERROR: {s['error']}")
            else:
                km = s.get("planned_distance_km")
                km_label = f"({km} km)" if km is not None else "(strength)"
                lines.append(
                    f"  {s['date']}  {s['workout_name']}  "
                    f"{km_label}  ID: {s.get('garmin_workout_id')}"
                )
        lines.append("\nAll sessions have been scheduled on your Garmin Connect calendar.")

        # Auto-flag the week as planned so the morning coach skips creation
        mark_week_planned(week_start.isoformat(), planned_by="generate_week")

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "mark_week_planned":
        week_start_str = _week_start_arg(arguments)
        planned_by = (arguments or {}).get("planned_by", "manual")
        mark_week_planned(week_start_str, planned_by=planned_by)
        return [types.TextContent(type="text", text=f"Week of {week_start_str} marked as planned (by: {planned_by}).")]

    elif name == "get_week_planned":
        week_start_str = _week_start_arg(arguments)
        result = is_week_planned(week_start_str)
        if result["planned"]:
            text = (
                f"WEEK PLANNED: {result['week_start']}\n"
                f"  Planned at: {result['planned_at']}\n"
                f"  Planned by: {result['planned_by']}"
            )
        else:
            text = f"NOT PLANNED: Week of {result['week_start']} has no plan flag."
        return [types.TextContent(type="text", text=text)]

    elif name == "get_race_strategy":
        plan               = load_plan()
        target_time_arg    = (arguments or {}).get("target_time")

        # Fetch current Garmin race prediction for gap analysis
        try:
            preds        = fetch_garmin_race_predictions()
            predicted_t  = preds.get("hm") if plan and plan.get("race_distance") == "half_marathon" else None
        except Exception:
            predicted_t = None

        text = build_race_strategy(plan, target_time_arg, predicted_t)
        return [types.TextContent(type="text", text=text)]

    elif name == "get_fitness_scores":
        scores = fetch_fitness_scores()
        lines  = [f"FITNESS SCORES — {date.today().strftime('%A %d %B %Y')}", ""]
        es = scores.get("endurance_score")
        el = scores.get("endurance_level")
        hs = scores.get("hill_score")
        hl = scores.get("hill_level")
        lines.append(
            f"  Endurance Score: {es if es is not None else 'N/A'}"
            + (f"  ({el})" if el else "")
        )
        lines.append(
            f"  Hill Score:      {hs if hs is not None else 'N/A'}"
            + (f"  ({hl})" if hl else "")
        )
        if es is None and hs is None:
            lines.append("\n  Neither score available — requires a compatible Garmin device and sufficient activity history.")
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "review_strength_workout":
        act_id_arg     = (arguments or {}).get("activity_id")
        apply_defaults = bool((arguments or {}).get("apply_defaults", False))

        data = fetch_strength_exercise_sets(int(act_id_arg) if act_id_arg else None)

        if data.get("error"):
            return [types.TextContent(type="text", text=data["error"])]

        activity_id     = data["activity_id"]
        all_defaults    = {d["name"].lower(): d for d in get_exercise_defaults()}
        garmin_defaults = {d["garmin_exercise_name"]: d
                           for d in get_exercise_defaults()
                           if d.get("garmin_exercise_name")}

        lines = [
            f"STRENGTH WORKOUT REVIEW",
            f"  Activity: {data['activity_name']}  ({data['date']})",
            f"  ID:       {activity_id}",
            "",
        ]

        to_update        = []  # exercises eligible for default update
        completion_log   = []  # rows to persist to exercise_completion_log
        progression_due  = []  # exercises where 2/3 sessions suggest a weight bump

        for ex in data["exercises"]:
            g_name   = ex["exercise_name"]
            display  = ex["name"]
            n_sets   = ex["sets_completed"]
            w_logged = ex["weight_kg"]

            # Find matching default — Garmin key first, alias fallback, display name last
            default = garmin_defaults.get(g_name)
            if not default:
                _, matched = lookup_garmin_exercise(display)
                if matched:
                    default = garmin_defaults.get(matched)
            if not default:
                default = all_defaults.get(display.lower())

            planned_sets   = default["sets"]      if default else None
            default_weight = default["weight_kg"] if default else None
            default_name   = default["name"]      if default else display

            fully_completed = (planned_sets is None) or (n_sets >= planned_sets)

            # Stage completion row for DB (written after the loop)
            completion_log.append({
                "garmin_exercise_name": g_name,
                "display_name":         display,
                "sets_completed":       n_sets,
                "sets_planned":         planned_sets,
                "weight_kg":            w_logged,
                "fully_completed":      fully_completed,
            })

            set_detail = "  ".join(
                f"{s['reps']}r"
                + (f"@{s['weight_kg']}kg" if s["weight_kg"] is not None else "")
                for s in ex["sets"]
            )
            scheme_str = f"{n_sets}/{planned_sets} sets" if planned_sets else f"{n_sets} sets"
            weight_str = f"{w_logged}kg" if w_logged is not None else "no weight logged"

            if fully_completed:
                if default is None:
                    icon   = "+"
                    status = "new — not in defaults"
                elif default_weight is None or w_logged is None:
                    icon   = "?"
                    status = "completed (no weight comparison)"
                elif abs(w_logged - default_weight) < 0.1:
                    icon   = "✓"
                    status = f"matched default ({default_weight}kg)"
                elif w_logged > default_weight:
                    icon   = "↑"
                    status = f"above default ({default_weight}kg → {w_logged}kg)"
                else:
                    icon   = "↓"
                    status = f"below default ({default_weight}kg → {w_logged}kg)"

                # Ratchet: only write a default when the logged weight is at or above the current
                # base (or the exercise is brand new). A lighter logged session — e.g. a sick/off
                # day — is reported above but never lowers the stored default.
                if w_logged is not None and (default_weight is None or w_logged >= default_weight):
                    to_update.append({
                        "name":                 default_name,
                        "weight_kg":            w_logged,
                        "sets":                 n_sets,
                        "garmin_category":      ex["category"] or None,
                        "garmin_exercise_name": g_name or None,
                    })

                # Check progression history (reads last 3 sessions already in DB)
                prog = check_progression_due(g_name, display)
                if prog["due"] and prog["suggestion_kg"] is not None:
                    progression_due.append({
                        "name":          default_name,
                        "current_kg":    w_logged,
                        "suggestion_kg": prog["suggestion_kg"],
                        "completions":   prog["completions"],
                        "sessions":      prog["sessions"],
                    })
            else:
                icon   = "✗"
                status = f"incomplete ({scheme_str})"

            lines.append(f"  {icon} {display:<35} {weight_str:<12} {scheme_str:<14} {status}")
            if set_detail:
                lines.append(f"      {set_detail}")

        # Persist completion log (idempotent — skipped if activity already logged)
        log_exercise_completions(activity_id, completion_log)

        lines.append("")

        # Progression suggestions
        if progression_due:
            lines.append("PROGRESSIVE OVERLOAD SUGGESTED (2/3 recent sessions completed):")
            for p in progression_due:
                lines.append(
                    f"  → {p['name']:<35} {p['current_kg']}kg → {p['suggestion_kg']}kg"
                    f"  ({p['completions']}/{p['sessions']} sessions)"
                )
            lines.append("")

        if apply_defaults and to_update:
            enriched = []
            for ex in to_update:
                if not ex.get("garmin_category") or not ex.get("garmin_exercise_name"):
                    cat, gname = lookup_garmin_exercise(ex["name"])
                    ex = dict(ex)
                    if cat:
                        ex.setdefault("garmin_category", cat)
                        ex.setdefault("garmin_exercise_name", gname)
                enriched.append(ex)
            results = set_exercise_defaults(enriched)
            lines.append("DEFAULTS UPDATED:")
            lines += [f"  {r}" for r in results]
        elif to_update:
            lines.append(
                f"{len(to_update)} exercise(s) eligible for default update. "
                "Call again with apply_defaults=true to save."
            )
        else:
            lines.append("No fully-completed exercises to update.")

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_zones":
        zones = get_zones()
        source   = zones.get("_source", "unavailable")
        lthr     = zones.get("_lthr")
        lt_pace  = zones.get("_lt_pace_sec")

        def _hr_str(z):
            hr = zones.get(z, {}).get("hr")
            return f"{hr[0]}–{hr[1]} bpm" if hr else "N/A"

        def _pace_str(z):
            p = zones.get(z, {}).get("pace")
            return f"{fmt_pace(p[0])} – {fmt_pace(p[1])}" if p else "N/A"

        if source == "unavailable":
            return [types.TextContent(
                type="text",
                text=(
                    "ZONES — N/A\n\n"
                    "Lactate threshold data not available from Garmin. "
                    "Complete a Garmin LT test or ensure your device supports LT detection."
                )
            )]

        lines = [
            f"TRAINING ZONES — {date.today().strftime('%A %d %B %Y')}",
            f"Source: Garmin lactate threshold",
            f"LTHR: {lthr or 'N/A'} bpm   LT Pace: {fmt_pace(lt_pace)}",
            "",
            f"{'Zone':<12} {'HR Range':<20} {'Pace Range':<25} Notes",
            "─" * 72,
            f"{'Easy':<12} {_hr_str('easy'):<20} {_pace_str('easy'):<25} Z1–2, conversational",
            f"{'Aerobic':<12} {_hr_str('aerobic'):<20} {_pace_str('aerobic'):<25} Z2–3, steady-state",
            f"{'Threshold':<12} {_hr_str('threshold'):<20} {_pace_str('threshold'):<25} Z4, comfortably hard",
            f"{'Interval':<12} {_hr_str('interval'):<20} {_pace_str('interval'):<25} Z5, VO2max",
            "─" * 72,
            "",
            f"Easy HR ceiling: {zones.get('easy', {}).get('hr', [None, None])[1] or 'N/A'} bpm",
            f"Threshold pace:  {fmt_pace(zones.get('threshold', {}).get('pace', [None])[0])} – "
            f"{fmt_pace((zones.get('threshold', {}).get('pace') or [None, None])[1])}",
        ]
        return [types.TextContent(type="text", text="\n".join(lines))]

    else:
        raise ValueError(f"Unknown tool: {name}")


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())

if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("=== Morning Metrics ===")
        data = fetch_morning_data()
        status, flags, decision = assess_readiness(data)
        print("DATA:", data)
        print("STATUS:", status)
        for f in flags:
            print(f"  - {f}")
        print("DECISION:", decision)

        print("\n=== Training Load ===")
        load = fetch_training_load()
        print(load)
        print(assess_training_state(load))

        print("\n=== Fitness Trend ===")
        for entry in fetch_fitness_trend():
            print(f"  {entry['date']}  VO2: {entry['vo2max']}")

        print("\n=== Recent Activities ===")
        for act in fetch_recent_activities():
            print(f"  {act['date']}  {act['name']}  {act['distance_km']}km  {act['avg_pace']}")
    else:
        asyncio.run(main())
