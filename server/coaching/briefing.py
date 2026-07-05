from datetime import date

from garmin.readiness import fetch_morning_data, assess_readiness
from garmin.schedule import fetch_scheduled_workout
from coaching.weather import resolve_location, fetch_weather_windows, find_best_run_window
from coaching.plan import load_plan, format_plan_context, _NO_PLAN_NUDGE
from db.history import log_coach_suggestion


def get_daily_briefing(location: str | None = None) -> str:
    """
    Single-call morning briefing:
      1. Readiness (HRV, sleep, TR score → GREEN/AMBER/RED)
      2. Today's planned session (from Garmin calendar)
      3. Best weather window
      4. Unified go / modify / rest verdict

    When readiness is RED or AMBER against a quality session, proposes a swap
    and logs it as a pending coaching suggestion.
    """
    today = date.today()

    # ── 1. Readiness ──────────────────────────────────────────────────────
    data = fetch_morning_data()
    status, flags, decision = assess_readiness(data)

    bb_str     = f"{data['body_battery']}/100" if data['body_battery']     is not None else "N/A"
    stress_str = f"{data['avg_stress']}/100"   if data['avg_stress']       is not None else "N/A"
    tr_str     = f"{data['tr_score']}/100"     if data.get('tr_score')     is not None else "N/A"
    hrv_str    = str(data['hrv']) if data['hrv'] is not None else "N/A"

    lines = [
        f"DAILY BRIEFING — {today.strftime('%A %d %B %Y')}",
        "",
        "READINESS:",
        f"  Status:            {status.upper()}",
        f"  HRV:               {hrv_str}" + (f"  [{data.get('hrv_status')}]" if data.get('hrv_status') else ""),
        f"  Resting HR:        {data['resting_hr'] or 'N/A'} bpm",
    ]

    if data.get('sleep_score') is not None:
        lines.append(f"  Sleep Score:       {data['sleep_score']}/100")
    elif data.get('sleep_hours'):
        lines.append(f"  Sleep:             {data['sleep_hours']}h")

    if data.get('deep_hours') or data.get('rem_hours'):
        lines.append(
            f"  Sleep stages:      Deep {data.get('deep_hours','?')}h  "
            f"REM {data.get('rem_hours','?')}h  "
            f"Light {data.get('light_hours','?')}h"
        )

    lines += [
        f"  Body Battery:      {bb_str}",
        f"  Avg Stress:        {stress_str}",
        f"  Training Readiness:{tr_str}",
    ]

    if data.get("stale_warnings"):
        for w in data["stale_warnings"]:
            lines.append(f"  ⚠ {w}")

    lines.append("")

    # ── 2. Today's session ────────────────────────────────────────────────
    today_str      = today.isoformat()
    scheduled      = fetch_scheduled_workout(today_str)
    workouts_today = scheduled.get("workouts") or []

    lines.append("TODAY'S PLAN:")
    is_quality_session = False
    session_names      = []

    if workouts_today:
        for w in workouts_today:
            wname = w.get("workoutName") or w.get("name") or "Workout"
            sport = (w.get("sportType") or {}).get("sportTypeKey", "")
            lines.append(f"  • {wname} ({sport})")
            session_names.append(wname)
            name_lower = wname.lower()
            if any(k in name_lower for k in ("interval", "tempo", "threshold", "speed", "race")):
                is_quality_session = True
    else:
        lines.append("  No workout scheduled on Garmin Connect for today.")

    lines.append("")

    # ── 3. Best weather window ────────────────────────────────────────────
    weather_note = ""
    try:
        lat, lon, place_label = resolve_location(location)
        is_weekday = today.weekday() < 5
        hours      = fetch_weather_windows(lat, lon)
        result     = find_best_run_window(hours, is_weekday)
        rec        = result.get("recommended")
        if rec:
            weather_note = (
                f"{rec['window']} — {rec['temp']:.0f}°C, {rec['weather_desc'].title()}, "
                f"{rec['precip_prob']}% rain  (score {rec['score']}/100)"
            )
            lines.append("BEST RUN WINDOW:")
            lines.append(f"  {weather_note}")
        else:
            lines.append("WEATHER: Unable to score windows for today.")
    except Exception:
        lines.append("WEATHER: Location unavailable — run get_run_window for details.")

    lines.append("")

    # ── 4. Verdict + swap suggestion ──────────────────────────────────────
    lines.append("VERDICT:")

    if status == "green":
        lines.append(f"  ✓ GO — {decision}")
        if is_quality_session:
            lines.append("  Quality session confirmed — execute as planned.")
    elif status == "amber":
        if is_quality_session:
            lines.append(
                f"  ~ MODIFY — You're AMBER but have a quality session scheduled."
            )
            lines.append(
                f"  Recommendation: swap to an easy 25–30 min run instead. "
                "Trying to hit quality numbers on fatigued legs risks injury and won't drive adaptation."
            )
            try:
                session_label = session_names[0] if session_names else "quality session"
                log_coach_suggestion(
                    trigger="daily_briefing",
                    context_summary=(
                        f"AMBER readiness on {today_str}. "
                        f"Scheduled: {session_label}. "
                        f"HRV: {data['hrv']}, TR: {data.get('tr_score')}, BB: {data['body_battery']}"
                    ),
                    recommendation=(
                        f"Swap '{session_label}' to an easy 25–30 min run today."
                    ),
                    rationale=(
                        "Readiness metrics are AMBER — quality work on a fatigued nervous system "
                        "carries injury risk and limited training benefit."
                    ),
                    action_type="adjust_intensity",
                )
            except Exception:
                pass
        else:
            lines.append(f"  ~ EASY — {decision}")
    else:  # red
        lines.append(f"  ✗ REST — {decision}")
        if workouts_today:
            lines.append(
                "  Consider using unschedule_workout to remove today's session "
                "and reschedule it for later in the week."
            )
            try:
                log_coach_suggestion(
                    trigger="daily_briefing",
                    context_summary=(
                        f"RED readiness on {today_str}. "
                        f"HRV: {data['hrv']}, BB: {data['body_battery']}, TR: {data.get('tr_score')}"
                    ),
                    recommendation="Rest today. Reschedule today's session to later in the week.",
                    rationale=(
                        "RED readiness signals the nervous system is not recovered. "
                        "Training through RED increases injury risk without meaningful fitness gains."
                    ),
                    action_type="rest_day",
                )
            except Exception:
                pass

    # ── Plan context ──────────────────────────────────────────────────────
    plan = load_plan()
    lines.append(format_plan_context(plan) if plan else _NO_PLAN_NUDGE)

    return "\n".join(lines)
