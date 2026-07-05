import os
from datetime import date
from pathlib import Path

from garmin.client import get_client

_profile    = os.environ.get("GARMIN_COACH_PROFILE", "")
_suffix     = f"-{_profile}" if _profile else ""
_HISTORY_DB = Path.home() / f".garmin-coach{_suffix}-history.db"


def grade_execution(data: dict) -> list[str]:
    """
    Compare this activity's actual output against what was planned for that date
    in the history DB. Returns a list of grading lines, or empty list if no
    planned data exists.

    Grades:
      - Distance completion (actual vs planned km)
      - Intensity fit: check dominant HR zone against workout type
    """
    act_date     = (data.get("date") or "")[:10]
    actual_km    = data.get("distance_km") or 0
    actual_hr    = data.get("avg_hr")
    hr_zones     = data.get("hr_zones") or []

    if not act_date:
        return []

    # Look up planned row from history DB
    try:
        import sqlite3
        db_path = _HISTORY_DB
        if not db_path.exists():
            return []
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT type, planned_distance_km FROM workouts WHERE date = ? ORDER BY id LIMIT 1",
            (act_date,),
        ).fetchone()
        conn.close()
        if not row:
            return []
        planned_km   = row["planned_distance_km"]
        workout_type = row["type"] or "unknown"
    except Exception:
        return []

    lines = []

    # Distance completion
    if planned_km and planned_km > 0:
        pct = round(actual_km / planned_km * 100)
        if pct >= 95:
            lines.append(f"Distance: {actual_km}km vs {planned_km}km planned ({pct}%) — complete")
        elif pct >= 80:
            lines.append(f"Distance: {actual_km}km vs {planned_km}km planned ({pct}%) — slightly short")
        else:
            lines.append(f"Distance: {actual_km}km vs {planned_km}km planned ({pct}%) — significantly short")

    # Intensity fit: compare dominant HR zone to workout type expectation
    if hr_zones and workout_type:
        total_secs = sum(z.get("time_sec", 0) for z in hr_zones)
        if total_secs > 0:
            dominant_zone = max(hr_zones, key=lambda z: z.get("time_sec", 0))
            dz = dominant_zone.get("zone") or 0
            hard_secs = sum(z.get("time_sec", 0) for z in hr_zones if (z.get("zone") or 0) >= 3)
            hard_pct  = round(hard_secs / total_secs * 100)

            if workout_type in ("easy_run", "recovery"):
                if hard_pct > 20:
                    lines.append(
                        f"Intensity: {hard_pct}% of run in Z3+ for a {workout_type} "
                        "— easier than planned next time."
                    )
                else:
                    lines.append(f"Intensity: {hard_pct}% hard for a {workout_type} — good execution.")
            elif workout_type in ("tempo", "threshold"):
                if 20 <= hard_pct <= 50:
                    lines.append(f"Intensity: {hard_pct}% hard for a {workout_type} — within target range.")
                elif hard_pct < 20:
                    lines.append(f"Intensity: only {hard_pct}% hard for a {workout_type} — may have run too easy.")
                else:
                    lines.append(f"Intensity: {hard_pct}% hard for a {workout_type} — pushed harder than threshold target.")
            elif workout_type in ("interval",):
                if hard_pct >= 30:
                    lines.append(f"Intensity: {hard_pct}% hard for intervals — good stimulus.")
                else:
                    lines.append(f"Intensity: only {hard_pct}% hard for intervals — were the recoveries too long?")

    return lines


def build_week_context(recent_activities: list[dict], load_data: dict | None) -> str:
    """
    One short block summarising the current 7-day training context, so single-run
    analysis isn't blind to what else happened this week (e.g. a threshold + VO2max
    session already done — don't tell the athlete to "add quality"). Returns "" if
    there's nothing to show.
    """
    acts = recent_activities or []
    if not acts:
        return ""

    # "Hard/quality" = meaningful anaerobic effort or a high aerobic training effect.
    hard = [a for a in acts
            if (a.get("anaerobic_te") or 0) >= 1.0 or (a.get("aerobic_te") or 0) >= 3.5]
    run_km = round(sum((a.get("distance_km") or 0) for a in acts
                       if "running" in (a.get("type") or "")), 1)
    status = (load_data or {}).get("training_status") or "N/A"
    focus  = (load_data or {}).get("load_focus")

    lines = [f"THIS WEEK SO FAR (last 7d): {len(acts)} session(s), {run_km} km running."]
    if hard:
        desc = ", ".join(f"{h.get('date')} {h.get('type')}" for h in hard[:3])
        lines.append(f"  Quality/hard already done: {len(hard)} ({desc}) — factor this before prescribing more intensity.")
    else:
        lines.append("  No hard/quality sessions in the last 7 days.")
    lines.append(f"  Training status: {status}" + (f"  |  load focus: {focus}" if focus else ""))
    return "\n".join(lines)


def format_run_analysis(data: dict, plan_context: str = "", week_context: str = "") -> str:
    lines = [
        f"RUN ANALYSIS — {data['date']}",
        f"  {data['activity_name']} ({data['activity_type']})",
        f"  Activity ID: {data['activity_id']}",
        "",
        f"BASICS:",
        f"  Distance:     {data['distance_km']} km",
        f"  Duration:     {data['duration_min']} min",
        f"  Avg Pace:     {data['avg_pace']}",
        f"  Avg HR:       {data['avg_hr'] or 'N/A'} bpm",
        f"  Max HR:       {data['max_hr'] or 'N/A'} bpm",
        f"  Calories:     {data['calories'] or 'N/A'}",
        f"  Aerobic TE:   {data['aerobic_te'] or 'N/A'}",
        f"  Anaerobic TE: {data['anaerobic_te'] or 'N/A'}",
    ]

    lines.append("\nRUNNING DYNAMICS:")
    dynamics = [
        ("Avg Cadence",            data.get("avg_cadence"),                    "spm"),
        ("Max Cadence",            data.get("max_cadence"),                    "spm"),
        ("Avg Stride Length",      data.get("avg_stride_length_m"),            "m"),
        ("Avg Ground Contact Time", data.get("avg_ground_contact_time_ms"),    "ms"),
        ("Ground Contact Balance", data.get("avg_ground_contact_balance_pct"), "% L"),
        ("Avg Vertical Oscillation", data.get("avg_vertical_oscillation_cm"),  "cm"),
        ("Avg Vertical Ratio",     data.get("avg_vertical_ratio_pct"),         "%"),
        ("Avg Power",              data.get("avg_power_w"),                    "W"),
        ("Max Power",              data.get("max_power_w"),                    "W"),
        ("Normalized Power",       data.get("normalized_power_w"),             "W"),
        ("Training Stress Score",  data.get("training_stress_score"),          ""),
    ]
    has_dynamics = False
    for label, val, unit in dynamics:
        if val is not None:
            has_dynamics = True
            if isinstance(val, float):
                lines.append(f"  {label + ':':<30} {val:.1f} {unit}")
            else:
                lines.append(f"  {label + ':':<30} {val} {unit}")
    if not has_dynamics:
        lines.append("  No running dynamics data available (requires compatible device).")

    if data["splits"]:
        split_label = "SPLITS (per km):" if data.get("splits_source") == "distance" else "SPLITS (laps):"
        lines.append(f"\n{split_label}")
        lines.append(f"  {'#':<4} {'Dist':>6} {'Pace':>10} {'HR':>6} {'Cadence':>8} {'Elev+':>7}")
        lines.append(f"  {'—'*4} {'—'*6} {'—'*10} {'—'*6} {'—'*8} {'—'*7}")
        hilly_splits = []
        for s in data["splits"]:
            hr_str   = str(s['avg_hr']) if s['avg_hr'] else "—"
            cad_str  = f"{s['avg_cadence']:.0f}" if s['avg_cadence'] else "—"
            elev_m   = s['elevation_gain']
            elev_str = f"{elev_m:.0f}m" if elev_m is not None else "—"

            # Flag splits where elevation gain > 15m/km (grade-adjusted pace caveat)
            elev_flag = ""
            if elev_m is not None and s['distance_km'] > 0:
                gain_per_km = elev_m / s['distance_km']
                if gain_per_km > 15:
                    elev_flag = " hilly"
                    hilly_splits.append(s['split'])

            lines.append(
                f"  {s['split']:<4} {s['distance_km']:>5.2f}km {s['pace']:>10} "
                f"{hr_str:>6} {cad_str:>8} {elev_str:>7}{elev_flag}"
            )
        if hilly_splits:
            lines.append(
                f"  Splits {', '.join(str(x) for x in hilly_splits)} had significant elevation — "
                "pace will look slower than effort; consider effort/HR rather than raw pace for hilly km."
            )

    if data["hr_zones"]:
        lines.append("\nHR ZONE BREAKDOWN:")
        for z in data["hr_zones"]:
            bar_len = round(z["pct_of_total"] / 2)
            bar = "█" * bar_len
            lines.append(
                f"  Zone {z['zone'] or '?'} ({z['range']}): "
                f"{z['time_min']:.1f} min ({z['pct_of_total']:.1f}%)  {bar}"
            )

    recs = analyze_running_form(data)
    lines.append("\nFORM ANALYSIS:")
    for rec in recs:
        lines.append(f"  • {rec}")

    # ── Execution grading (planned vs actual) ─────────────────────────────
    grading = grade_execution(data)
    if grading:
        lines.append("\nEXECUTION GRADE:")
        for g in grading:
            lines.append(f"  {g}")

    # ── Weekly context (so a single run isn't judged in isolation) ────────
    if week_context:
        lines.append(f"\n{week_context}")

    lines.append(plan_context)
    return "\n".join(lines)


def fetch_run_analysis(activity_id: str | int | None = None) -> dict:
    """
    Fetch full running dynamics, splits, and HR zones for a single activity.

    If activity_id is None, finds the most recent running activity.
    Returns a dict with all available running dynamics, splits, and HR zone data.
    """
    client = get_client()

    # ── Resolve activity ID (find most recent run if not specified) ────────
    if activity_id is None:
        acts = client.get_activities(0, 10)
        run_types = {"running", "trail_running", "treadmill_running", "track_running"}
        for act in acts:
            act_type = act.get("activityType", {}).get("typeKey", "")
            if act_type in run_types:
                activity_id = act.get("activityId")
                break
        if activity_id is None:
            return {"error": "No recent running activity found in the last 10 activities."}

    activity_id = str(activity_id)

    # ── Fetch activity summary (contains running dynamics aggregates) ─────
    try:
        summary_raw = client.get_activity(activity_id)
    except Exception as exc:
        return {"error": f"Could not fetch activity {activity_id}: {exc}"}

    # Garmin's /activity-service/activity/{id} can return running dynamics
    # either at the top level OR nested inside summaryDTO. Merge both so
    # field lookups below always find the right value regardless of shape.
    summary = {**summary_raw}
    if isinstance(summary_raw.get("summaryDTO"), dict):
        summary.update(summary_raw["summaryDTO"])

    # ── Fetch splits ──────────────────────────────────────────────────────
    # Prefer per-km distance auto-splits (split_summaries) over lap-button
    # splits.  Lap-button splits produce tiny segments (0.02 km, 0.11 km)
    # wherever the user pressed the lap button to mark workout phases
    # (warmup end, interval start, etc.), making the output unreadable.
    # split_summaries gives clean ~1 km segments regardless of lap presses.
    splits = None
    splits_source = None          # "distance" or "lap"
    try:
        raw_summaries = client.get_activity_split_summaries(activity_id)
        if raw_summaries:
            # split_summaries groups by splitType — pick the active running
            # segments which contain per-km distance splits.
            _ACTIVE_TYPES = {
                "RUN_ACTIVE", "INTERVAL_ACTIVE", "run_active",
                "interval_active",
            }
            summaries_list = (
                raw_summaries.get("splitSummaries")
                or raw_summaries.get("splits")
                or (raw_summaries if isinstance(raw_summaries, list) else [])
            )
            for group in summaries_list:
                stype = group.get("splitType") or ""
                if stype.upper() in {t.upper() for t in _ACTIVE_TYPES}:
                    inner = group.get("splits") or []
                    if inner:
                        splits = {"distanceSplits": inner}
                        splits_source = "distance"
                        break
            # If no active-type group found, take the first group that has
            # per-km-ish splits (distance ≈ 1000 m each).
            if splits is None:
                for group in summaries_list:
                    inner = group.get("splits") or []
                    if inner and len(inner) >= 2:
                        avg_dist = sum(
                            s.get("distance") or 0 for s in inner
                        ) / len(inner)
                        if 800 < avg_dist < 1800:  # ~1 km per split
                            splits = {"distanceSplits": inner}
                            splits_source = "distance"
                            break
    except Exception:
        pass

    # Fall back to lap-button splits if distance splits unavailable
    if splits is None:
        try:
            splits = client.get_activity_splits(activity_id)
            splits_source = "lap"
        except Exception:
            pass

    # ── Fetch HR zone breakdown ───────────────────────────────────────────
    hr_zones = None
    try:
        hr_zones = client.get_activity_hr_in_timezones(activity_id)
    except Exception:
        pass

    # ── Extract basics ────────────────────────────────────────────────────
    distance_km = round((summary.get("distance") or 0) / 1000, 2)
    duration_sec = summary.get("duration") or 0
    duration_min = round(duration_sec / 60, 1)
    avg_hr = summary.get("averageHR")
    max_hr = summary.get("maxHR")
    calories = summary.get("calories")

    if distance_km > 0 and duration_sec > 0:
        pace_sec = round(duration_sec / distance_km)
        avg_pace = f"{pace_sec // 60}:{pace_sec % 60:02d}/km"
    else:
        avg_pace = "N/A"

    aerobic_te = summary.get("trainingEffect")
    anaerobic_te = summary.get("anaerobicTrainingEffect")

    # ── Extract running dynamics ──────────────────────────────────────────
    avg_cadence = summary.get("averageRunCadence")
    max_cadence = summary.get("maxRunCadence")
    avg_stride_length = summary.get("strideLength")
    avg_ground_contact_time = summary.get("groundContactTime")      # ms
    avg_ground_contact_balance = summary.get("groundContactBalanceLeft")  # % left
    avg_vertical_oscillation = summary.get("verticalOscillation")   # cm
    avg_vertical_ratio = summary.get("verticalRatio")               # %
    avg_power = summary.get("averagePower")                         # watts
    max_power = summary.get("maxPower")
    normalized_power = summary.get("normalizedPower")
    training_stress_score = summary.get("trainingStressScore")

    # Some devices store stride length in cm, normalise to metres
    if avg_stride_length and avg_stride_length > 10:
        avg_stride_length = round(avg_stride_length / 100, 2)  # cm → m
    elif avg_stride_length:
        avg_stride_length = round(avg_stride_length, 2)

    # ── Parse splits ──────────────────────────────────────────────────────
    parsed_splits = []
    if splits:
        # Resolve the list of split dicts from whichever source we used.
        if splits_source == "distance":
            split_list = splits.get("distanceSplits") or []
        else:
            split_list = (
                splits.get("lapDTOs")
                or splits.get("splitSummaries")
                or (splits if isinstance(splits, list) else [])
            )

        for i, s in enumerate(split_list):
            s_dist = round((s.get("distance") or 0) / 1000, 2)
            s_dur = (
                s.get("duration")
                or s.get("movingDuration")
                or s.get("elapsedDuration")
                or 0
            )
            s_dur_sec = s_dur if s_dur < 10000 else s_dur / 1000  # handle ms vs s
            if s_dist > 0 and s_dur_sec > 0:
                s_pace_sec = round(s_dur_sec / s_dist)
                s_pace = f"{s_pace_sec // 60}:{s_pace_sec % 60:02d}/km"
            else:
                s_pace = "N/A"

            # split_summaries uses totalAscent; lap splits use elevationGain
            elev = s.get("elevationGain") or s.get("totalAscent")

            parsed_splits.append({
                "split": i + 1,
                "distance_km": s_dist,
                "duration_sec": round(s_dur_sec, 1),
                "pace": s_pace,
                "avg_hr": s.get("averageHR"),
                "max_hr": s.get("maxHR"),
                "avg_cadence": s.get("averageRunCadence"),
                "elevation_gain": elev,
            })

    # ── Parse HR zones ────────────────────────────────────────────────────
    parsed_hr_zones = []
    if hr_zones:
        zone_list = hr_zones if isinstance(hr_zones, list) else hr_zones.get("hrTimeInZones") or []
        for z in zone_list:
            secs = z.get("secsInZone") or 0
            zone_num = z.get("zoneNumber") or z.get("zone")
            zone_low = z.get("zoneLowBoundary")
            zone_high = z.get("zoneHighBoundary")
            if secs > 0 or zone_num is not None:
                pct = round(secs / duration_sec * 100, 1) if duration_sec > 0 else 0
                parsed_hr_zones.append({
                    "zone": zone_num,
                    "range": f"{zone_low or '?'}–{zone_high or '?'} bpm" if zone_low else "N/A",
                    "time_sec": round(secs),
                    "time_min": round(secs / 60, 1),
                    "pct_of_total": pct,
                })

    return {
        "activity_id": activity_id,
        "activity_name": summary_raw.get("activityName", "Activity"),
        "activity_type": summary_raw.get("activityTypeDTO", {}).get("typeKey", "unknown"),
        "date": (summary.get("startTimeLocal") or "")[:10],
        "distance_km": distance_km,
        "duration_min": duration_min,
        "avg_pace": avg_pace,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "calories": calories,
        "aerobic_te": aerobic_te,
        "anaerobic_te": anaerobic_te,
        # Running dynamics
        "avg_cadence": avg_cadence,
        "max_cadence": max_cadence,
        "avg_stride_length_m": avg_stride_length,
        "avg_ground_contact_time_ms": avg_ground_contact_time,
        "avg_ground_contact_balance_pct": avg_ground_contact_balance,
        "avg_vertical_oscillation_cm": avg_vertical_oscillation,
        "avg_vertical_ratio_pct": avg_vertical_ratio,
        "avg_power_w": avg_power,
        "max_power_w": max_power,
        "normalized_power_w": normalized_power,
        "training_stress_score": training_stress_score,
        # Splits and zones
        "splits": parsed_splits,
        "splits_source": splits_source,   # "distance" or "lap"
        "hr_zones": parsed_hr_zones,
    }


def analyze_running_form(data: dict) -> list[str]:
    """
    Produce actionable form recommendations based on running dynamics data.
    Returns a list of recommendation strings.
    """
    recs: list[str] = []

    # ── Cadence ───────────────────────────────────────────────────────────
    cadence = data.get("avg_cadence")
    if cadence is not None:
        if cadence < 160:
            recs.append(
                f"CADENCE: {cadence:.0f} spm — quite low. Optimal range is 170–185 spm. "
                "Low cadence often means over-striding, which increases braking forces and injury risk. "
                "Drill: use a metronome app at 170 bpm for short easy runs to retrain turnover."
            )
        elif cadence < 170:
            recs.append(
                f"CADENCE: {cadence:.0f} spm — slightly below optimal (170–185 spm). "
                "Try strides (6–8×20s at the end of easy runs) focusing on quick, light foot turnover."
            )
        elif cadence <= 185:
            recs.append(f"CADENCE: {cadence:.0f} spm — in the optimal 170–185 range. Good turnover.")
        else:
            recs.append(
                f"CADENCE: {cadence:.0f} spm — above typical range. Fine if stride length isn't suffering. "
                "Monitor that you're not shuffling — stride length should stay above ~1.0m at easy pace."
            )

    # ── Ground contact time ───────────────────────────────────────────────
    gct = data.get("avg_ground_contact_time_ms")
    if gct is not None:
        if gct > 280:
            recs.append(
                f"GROUND CONTACT TIME: {gct:.0f} ms — high (target <260ms for efficient running). "
                "This often correlates with over-striding or weak glutes/calves. "
                "Drills: A-skips, single-leg calf raises, and hill sprints to build reactive stiffness."
            )
        elif gct > 260:
            recs.append(
                f"GROUND CONTACT TIME: {gct:.0f} ms — slightly elevated (target <260ms). "
                "Plyometric drills (jump rope, bounding) can help reduce ground contact time."
            )
        elif gct >= 200:
            recs.append(f"GROUND CONTACT TIME: {gct:.0f} ms — efficient. Keep it up.")
        else:
            recs.append(f"GROUND CONTACT TIME: {gct:.0f} ms — very quick. Elite-level ground contact.")

    # ── Ground contact balance ────────────────────────────────────────────
    gcb = data.get("avg_ground_contact_balance_pct")
    if gcb is not None:
        # Garmin reports as left-side %, so 50.0 = perfect balance
        deviation = abs(gcb - 50.0)
        if deviation > 2.0:
            side = "left" if gcb > 50.0 else "right"
            recs.append(
                f"GROUND CONTACT BALANCE: {gcb:.1f}% L / {100-gcb:.1f}% R — "
                f"imbalance ({deviation:.1f}% bias to {side}). "
                "Asymmetries >2% may indicate a strength deficit or mobility restriction on one side. "
                "Single-leg strength work (Bulgarian split squats, single-leg deadlifts) can help even this out."
            )
        elif deviation > 1.0:
            recs.append(
                f"GROUND CONTACT BALANCE: {gcb:.1f}% L / {100-gcb:.1f}% R — "
                f"minor asymmetry ({deviation:.1f}%). Monitor but not a concern."
            )
        else:
            recs.append(
                f"GROUND CONTACT BALANCE: {gcb:.1f}% L / {100-gcb:.1f}% R — well balanced."
            )

    # ── Vertical oscillation ──────────────────────────────────────────────
    vo = data.get("avg_vertical_oscillation_cm")
    if vo is not None:
        if vo > 10.0:
            recs.append(
                f"VERTICAL OSCILLATION: {vo:.1f} cm — high (target 6–10 cm). "
                "Too much bounce wastes energy going up instead of forward. "
                "Cue: 'run tall, hips forward' — imagine a ceiling just above your head. "
                "Drills: A-skips and quick-feet ladder work."
            )
        elif vo > 8.5:
            recs.append(
                f"VERTICAL OSCILLATION: {vo:.1f} cm — slightly elevated. "
                "Some bounce is normal, but you can gain efficiency by thinking 'glide, don't bounce'."
            )
        elif vo >= 6.0:
            recs.append(f"VERTICAL OSCILLATION: {vo:.1f} cm — efficient range (6–10 cm).")
        else:
            recs.append(f"VERTICAL OSCILLATION: {vo:.1f} cm — very low. Smooth, efficient stride.")

    # ── Vertical ratio ────────────────────────────────────────────────────
    vr = data.get("avg_vertical_ratio_pct")
    if vr is not None:
        if vr > 10.0:
            recs.append(
                f"VERTICAL RATIO: {vr:.1f}% — high (target <8%). "
                "This confirms too much vertical movement relative to stride length. "
                "Work on both reducing bounce and lengthening stride through hip extension drills."
            )
        elif vr > 8.0:
            recs.append(f"VERTICAL RATIO: {vr:.1f}% — slightly above optimal (<8%). Room for improvement.")
        else:
            recs.append(f"VERTICAL RATIO: {vr:.1f}% — good. Efficient forward propulsion.")

    # ── Stride length ─────────────────────────────────────────────────────
    sl = data.get("avg_stride_length_m")
    if sl is not None:
        if sl < 0.9:
            recs.append(
                f"STRIDE LENGTH: {sl:.2f} m — short. This may indicate shuffling or limited hip extension. "
                "Glute activation drills and hip flexor stretching can help open up the stride."
            )
        elif sl < 1.05:
            recs.append(
                f"STRIDE LENGTH: {sl:.2f} m — moderate. Appropriate for easy pace; "
                "should increase naturally at faster paces."
            )
        elif sl <= 1.40:
            recs.append(f"STRIDE LENGTH: {sl:.2f} m — good range for most running paces.")
        else:
            recs.append(
                f"STRIDE LENGTH: {sl:.2f} m — long. Make sure you're not over-striding — "
                "foot should land under your centre of mass, not out in front."
            )

    # ── Running power ─────────────────────────────────────────────────────
    power = data.get("avg_power_w")
    if power is not None:
        recs.append(
            f"RUNNING POWER: {power:.0f}W avg"
            + (f" / {data['max_power_w']:.0f}W max" if data.get("max_power_w") else "")
            + (f" / {data['normalized_power_w']:.0f}W normalized" if data.get("normalized_power_w") else "")
            + ". Power data helps track efficiency over time — compare across similar sessions."
        )

    if not recs:
        recs.append(
            "No running dynamics data available for this activity. "
            "Running dynamics require a compatible device (e.g. Garmin HRM-Pro, RD Pod, or newer watches)."
        )

    return recs
