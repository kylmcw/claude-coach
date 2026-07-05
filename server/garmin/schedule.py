from collections import defaultdict
from datetime import date, timedelta

from garmin.client import get_client


def _workout_items(month_data) -> list[tuple[str, dict]]:
    """Yield (item_date_iso, {"workout_id", "schedule"}) for each workout-shaped
    calendar item in a get_scheduled_workouts() payload.

    Garmin returns {"calendarItems": [...]} on the calendar-service endpoint, but
    key names drift across endpoints/versions — sniff the documented shape and
    fall back to known aliases rather than trust one spelling.
    """
    if isinstance(month_data, list):
        items = month_data
    else:
        items = (
            month_data.get("calendarItems")
            or month_data.get("workoutSchedules")
            or month_data.get("items")
            or []
        )

    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_date = (
            item.get("date")
            or item.get("scheduleDate")
            or item.get("scheduledDate")
            or item.get("calendarDate")
        )
        item_type = (item.get("itemType") or "").lower()
        if item_type and item_type != "workout":
            continue  # skip logged activities, notes
        workout_id = item.get("workoutId") or item.get("workout_id") or item.get("id")
        if not item_date or not workout_id:
            continue
        out.append((item_date, {"workout_id": workout_id, "schedule": item}))
    return out


def _fetch_full_workouts(client, matches: list[dict]) -> list[dict]:
    """Resolve each {"workout_id", "schedule"} match to its full workout dict,
    stashing the schedule metadata under _scheduleMeta for the formatter."""
    workouts = []
    for m in matches:
        try:
            full = client.get_workout_by_id(m["workout_id"])
        except Exception as e:
            full = {"_fetch_error": str(e), "workoutId": m["workout_id"]}
        if isinstance(full, dict):
            full.setdefault("_scheduleMeta", m["schedule"])
        workouts.append(full)
    return workouts


def fetch_scheduled_workout(date_str: str | None = None) -> dict:
    """
    Look up workouts scheduled on the Garmin calendar for a given date.

    Args:
        date_str: Target date in YYYY-MM-DD format. Defaults to tomorrow
                  (in the user's local timezone).

    Returns:
        {
          "date":      "YYYY-MM-DD",
          "workouts":  [<full workout dict from get_workout_by_id>, ...],
        }
        `workouts` is empty when nothing is scheduled on that date.
    """
    if date_str:
        target = date.fromisoformat(date_str)
    else:
        target = date.today() + timedelta(days=1)
    target_iso = target.isoformat()

    client = get_client()
    month_data = client.get_scheduled_workouts(target.year, target.month) or {}

    matches = [m for d, m in _workout_items(month_data) if d == target_iso]
    workouts = _fetch_full_workouts(client, matches)

    return {"date": target_iso, "workouts": workouts}


def fetch_future_schedule(days: int = 7) -> list[dict]:
    """
    Return scheduled workouts for the next `days` days starting from today.

    Groups dates by calendar month so get_scheduled_workouts() is called
    at most twice (when the window spans a month boundary) rather than
    once per day.

    Returns:
        List of {"date": "YYYY-MM-DD", "workouts": [...]} dicts,
        one entry per day, in ascending date order.
        `workouts` is an empty list when nothing is scheduled that day.
    """
    days = max(1, min(days, 90))  # clamp to a sane range
    today = date.today()
    dates = [today + timedelta(days=i) for i in range(days)]

    # Group by (year, month) to minimise API calls
    month_map: dict[tuple, list] = defaultdict(list)
    for d in dates:
        month_map[(d.year, d.month)].append(d)

    client = get_client()

    # Fetch each unique month once, collect calendar items keyed by ISO date
    items_by_date: dict[str, list[dict]] = {d.isoformat(): [] for d in dates}

    for (year, month) in month_map:
        month_data = client.get_scheduled_workouts(year, month) or {}
        for item_date, match in _workout_items(month_data):
            if item_date in items_by_date:
                items_by_date[item_date].append(match)

    # Fetch full workout details for each matched entry
    return [
        {"date": d.isoformat(),
         "workouts": _fetch_full_workouts(client, items_by_date[d.isoformat()])}
        for d in dates
    ]


# ─── Scheduled workout formatter ─────────────────────────────────────────────

_PACE_TARGET_KEYS  = {"speed.zone", "pace.zone"}
_HR_TARGET_KEYS    = {"heart.rate.zone"}
_POWER_TARGET_KEYS = {"power.zone"}


def _fmt_distance(meters: float | int | None) -> str:
    if meters is None:
        return "?"
    m = float(meters)
    if m >= 1000:
        return f"{m / 1000:.2f} km"
    return f"{int(round(m))} m"


def _fmt_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    s = int(round(float(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _fmt_pace_from_mps(mps: float) -> str:
    """Convert m/s to min:sec per km running pace."""
    if mps <= 0:
        return "?"
    sec_per_km = 1000.0 / mps
    mins = int(sec_per_km // 60)
    secs = int(round(sec_per_km - mins * 60))
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/km"


def _step_end_condition(step: dict) -> str:
    cond = (step.get("endCondition") or {})
    key  = cond.get("conditionTypeKey")
    val  = step.get("endConditionValue")
    if key == "time":
        return f"for {_fmt_duration(val)}"
    if key == "distance":
        return f"for {_fmt_distance(val)}"
    if key == "reps":
        try:
            return f"{int(float(val))} reps"
        except (TypeError, ValueError):
            return f"{val} reps"
    if key == "fixed.rest":
        return f"rest {_fmt_duration(val)}"
    if key == "iterations":
        try:
            return f"x {int(float(val))}"
        except (TypeError, ValueError):
            return f"x {val}"
    if key in ("lap.button", "button.lap"):
        return "until lap button"
    if key == "calories":
        return f"{val} kcal"
    if val is not None:
        return f"{key or 'end'} {val}"
    return key or "open"


def _step_target(step: dict) -> str | None:
    target = step.get("targetType") or {}
    key    = target.get("workoutTargetTypeKey")
    if not key or key in ("no.target", "open"):
        return None
    t1 = step.get("targetValueOne")
    t2 = step.get("targetValueTwo")
    zone = step.get("zoneNumber")

    if key in _PACE_TARGET_KEYS:
        # Garmin stores speed targets as m/s; pace = inverse.
        if t1 and t2:
            return f"pace {_fmt_pace_from_mps(max(t1, t2))}–{_fmt_pace_from_mps(min(t1, t2))}"
        if zone:
            return f"pace zone {zone}"
        return "pace target"
    if key in _HR_TARGET_KEYS:
        if zone:
            return f"HR zone {zone}"
        if t1 and t2:
            return f"HR {int(t1)}–{int(t2)} bpm"
        return "HR target"
    if key in _POWER_TARGET_KEYS:
        if t1 and t2:
            return f"power {int(t1)}–{int(t2)} W"
        if zone:
            return f"power zone {zone}"
        return "power target"
    if key == "cadence":
        if t1 and t2:
            return f"cadence {int(t1)}–{int(t2)} spm"
        return "cadence target"
    return key


def _fmt_step(step: dict, indent: int = 2) -> list[str]:
    """Render one workout step (or a repeat group) as text lines."""
    pad = " " * indent
    step_type = (step.get("stepType") or {}).get("stepTypeKey") or step.get("type") or "step"

    if step.get("type") == "RepeatGroupDTO" or step_type == "repeat":
        iters = step.get("numberOfIterations") or step.get("endConditionValue")
        try:
            iters_int = int(float(iters)) if iters is not None else None
        except (TypeError, ValueError):
            iters_int = None
        header = f"{pad}Repeat x {iters_int}" if iters_int else f"{pad}Repeat"
        lines = [header]
        for inner in step.get("workoutSteps") or []:
            lines.extend(_fmt_step(inner, indent + 2))
        return lines

    # Executable step
    label = step_type.replace("_", " ").replace(".", " ").title()

    # Strength step: prefer description / exerciseName
    exercise_name = step.get("exerciseName")
    description   = step.get("description")
    weight        = step.get("weightValue")
    weight_unit   = step.get("weightDisplayUnit") or "kg"

    extras = []
    extras.append(_step_end_condition(step))
    tgt = _step_target(step)
    if tgt:
        extras.append(tgt)
    if weight:
        try:
            extras.append(f"@ {float(weight):.1f} {weight_unit}")
        except (TypeError, ValueError):
            extras.append(f"@ {weight} {weight_unit}")

    if exercise_name and exercise_name != "UNKNOWN":
        name_part = exercise_name.replace("_", " ").title()
    elif description:
        name_part = description
    else:
        name_part = label

    return [f"{pad}- {name_part}: {' | '.join(x for x in extras if x)}"]


def _format_scheduled_workout(date_str: str, workouts: list[dict]) -> str:
    """Render the fetched scheduled workouts as a human-readable summary."""
    if not workouts:
        return f"No workout scheduled for {date_str}."

    lines = [f"SCHEDULED WORKOUT(S) — {date_str}"]
    for idx, w in enumerate(workouts, start=1):
        name = w.get("workoutName") or w.get("name") or "Untitled workout"
        sport = (
            (w.get("sportType") or {}).get("sportTypeKey")
            or (w.get("_scheduleMeta") or {}).get("sportTypeKey")
            or "unknown"
        )
        sport_label = sport.replace("_", " ")
        prefix = f"\n[{idx}] " if len(workouts) > 1 else "\n"
        lines.append(f"{prefix}{name}  ({sport_label})")

        # Surface IDs so the user can act on them
        meta = w.get("_scheduleMeta") or {}
        template_id  = w.get("workoutId") or w.get("workout_id")
        calendar_id  = meta.get("id") or meta.get("scheduleId") or meta.get("calendarItemId")
        if template_id:
            lines.append(f"  Workout template ID: {template_id}  (use with delete_workout)")
        if calendar_id:
            lines.append(f"  Scheduled entry ID:  {calendar_id}  (use with unschedule_workout)")

        if w.get("_fetch_error"):
            lines.append(f"  (could not fetch full details: {w['_fetch_error']})")
            continue

        est = w.get("estimatedDurationInSecs")
        dist = w.get("estimatedDistanceInMeters") or w.get("estimatedDistance")
        meta_bits = []
        if est:
            meta_bits.append(f"est. {_fmt_duration(est)}")
        if dist:
            meta_bits.append(f"~{_fmt_distance(dist)}")
        if meta_bits:
            lines.append(f"  ({', '.join(meta_bits)})")

        desc = w.get("description")
        if desc:
            lines.append(f"  Notes: {desc}")

        segments = w.get("workoutSegments") or []
        step_lines: list[str] = []
        for seg in segments:
            for step in seg.get("workoutSteps") or []:
                step_lines.extend(_fmt_step(step, indent=2))

        if step_lines:
            lines.append("  Steps:")
            lines.extend(step_lines)
        else:
            lines.append("  (no structured steps in workout)")

    return "\n".join(lines)


def _format_future_schedule(entries: list[dict]) -> str:
    """Render a rolling multi-day schedule as a human-readable summary."""
    lines = [f"UPCOMING SCHEDULE — next {len(entries)} day(s) from {entries[0]['date']}\n"]
    for entry in entries:
        date_str = entry["date"]
        workouts = entry["workouts"]
        if not workouts:
            lines.append(f"  {date_str}  —  (nothing scheduled)")
        else:
            for w in workouts:
                name  = w.get("workoutName") or w.get("name") or "Unnamed workout"
                sport = (
                    (w.get("sportType") or {}).get("sportTypeKey")
                    or (w.get("_scheduleMeta") or {}).get("sportTypeKey")
                    or ""
                )
                sport_label = f" [{sport.replace('_', ' ')}]" if sport else ""
                meta  = w.get("_scheduleMeta") or {}
                cal_id = meta.get("id") or meta.get("scheduleId") or meta.get("calendarItemId")
                id_note = f"  (scheduled entry ID: {cal_id})" if cal_id else ""
                lines.append(f"  {date_str}  —  {name}{sport_label}{id_note}")
    return "\n".join(lines)
