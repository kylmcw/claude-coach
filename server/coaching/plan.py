import json
from datetime import date, timedelta
from pathlib import Path

from garmin.client import get_client
from utils import get_profile_suffix

# ─── Training plan config ─────────────────────────────────────────────────────
PLAN_FILE = Path.home() / f".garmin-coach{get_profile_suffix()}-plan.json"

_NO_PLAN_NUDGE = (
    "\n\n💡 No training plan set up. "
    "Call setup_training_plan (no arguments needed) to get started — "
    "the coach will pull your Garmin race predictions and walk you through the questions."
)


def load_plan() -> dict | None:
    """Load the training plan from disk. Returns None if no plan exists."""
    if not PLAN_FILE.exists():
        return None
    try:
        return json.loads(PLAN_FILE.read_text())
    except Exception:
        return None


def save_plan(plan: dict) -> None:
    """Persist the training plan to disk."""
    PLAN_FILE.write_text(json.dumps(plan, indent=2))


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


def format_plan_context(plan: dict) -> str:
    today = date.today()
    race_date = date.fromisoformat(plan["race_date"])
    days_to_race = (race_date - today).days

    created_on  = date.fromisoformat(plan["created_on"])
    total_weeks = plan["total_weeks"]
    weeks_elapsed = (today - created_on).days // 7
    current_week  = min(weeks_elapsed + 1, total_weeks)
    phase         = get_plan_phase(current_week, total_weeks)

    if days_to_race < 0:
        countdown = f"{abs(days_to_race)}d ago"
    elif days_to_race == 0:
        countdown = "TODAY"
    else:
        weeks_part = days_to_race // 7
        days_part  = days_to_race % 7
        countdown  = f"{weeks_part}w {days_part}d" if weeks_part else f"{days_part}d"

    lines = [
        "\n─── PLAN CONTEXT ────────────────────────────────────────────────────────────",
        f"  Race:      {plan['race_name']} ({plan['race_distance']}) — {race_date.strftime('%d %b %Y')} ({countdown})",
        f"  Progress:  Week {current_week} of {total_weeks} — {phase}",
    ]

    if plan.get("b_race_name") and plan.get("b_race_date"):
        b_date = date.fromisoformat(plan["b_race_date"])
        b_days = (b_date - today).days
        if b_days >= 0:
            b_weeks = b_days // 7
            b_dd    = b_days % 7
            b_count = f"{b_weeks}w {b_dd}d" if b_weeks else f"{b_dd}d"
            lines.append(f"  B Race:    {plan['b_race_name']} — {b_date.strftime('%d %b %Y')} ({b_count})")

    if plan.get("target_time"):
        lines.append(f"  Target:    {plan['target_time']} (predicted: {plan.get('predicted_hm_time', 'N/A')})")

    methodology_note = {
        "Base":        "easy aerobic work only — build the engine, hold back on intensity",
        "Development": "introduce one quality session/week (tempo or lactate threshold)",
        "Specific":    "race-pace work and intervals — this is where the gains get banked",
        "Taper":       "cut volume 40–50%, keep a touch of intensity — arrive fresh",
        "Race Week":   "minimal running — legs up, stay loose, execute on race day",
    }.get(phase, "")
    if methodology_note:
        lines.append(f"  Phase tip: {methodology_note}")

    lines.append("─────────────────────────────────────────────────────────────────────────────")
    return "\n".join(lines)


def _fetch_running_history_summary() -> dict:
    try:
        client  = get_client()
        today   = date.today()
        cutoff  = today - timedelta(days=28)
        acts    = client.get_activities(0, 50)

        km_by_week: dict[int, float] = {}
        runs_by_week: dict[int, int] = {}

        for act in acts:
            act_date_str = act.get("startTimeLocal", "")[:10]
            try:
                act_date = date.fromisoformat(act_date_str)
            except ValueError:
                continue
            if act_date < cutoff:
                continue

            act_type = (act.get("activityType") or {}).get("typeKey", "")
            if "running" not in act_type and act_type != "trail_running":
                continue

            week = (today - act_date).days // 7
            km   = (act.get("distance") or 0) / 1000
            km_by_week[week]   = km_by_week.get(week, 0) + km
            runs_by_week[week] = runs_by_week.get(week, 0) + 1

        if not km_by_week:
            return {"avg_weekly_km": 0.0, "avg_runs_per_week": 0.0, "has_history": False}

        n = len(km_by_week)
        avg_km   = round(sum(km_by_week.values()) / n, 1)
        avg_runs = round(sum(runs_by_week.values()) / n, 1)

        return {
            "avg_weekly_km":    avg_km,
            "avg_runs_per_week": avg_runs,
            "has_history":      avg_km >= 10,
        }
    except Exception:
        return {"avg_weekly_km": 0.0, "avg_runs_per_week": 0.0, "has_history": False}


def _recommend_methodology(
    run_days: int,
    planned_weekly_km: float | None,
    volume_change: str | None,
) -> tuple[str, str]:
    starting_fresh = volume_change == "starting_fresh"
    low_volume     = planned_weekly_km is not None and planned_weekly_km < 20

    if starting_fresh or low_volume:
        return (
            "polarized",
            "Building from a low base — polarized keeps easy runs genuinely easy "
            "so each quality session lands on fresh legs.",
        )

    if run_days <= 3:
        return (
            "polarized",
            f"With {run_days} run days/week, polarized squeezes maximum adaptation "
            "from limited sessions without residual fatigue piling up.",
        )

    if run_days >= 4 and (planned_weekly_km is None or planned_weekly_km >= 30):
        km_label = f"{planned_weekly_km}km/week" if planned_weekly_km else "your target volume"
        return (
            "pyramidal",
            f"With {run_days} run days and {km_label}, pyramidal suits the load — "
            "a moderate tempo middle zone builds aerobic capacity alongside the easy base.",
        )

    return (
        "polarized",
        "Polarized gives the clearest stress/recovery contrast "
        "and the strongest evidence base for half marathon improvement.",
    )


def build_setup_questionnaire(preds: dict) -> str:
    fitness_lines = ["GARMIN FITNESS DATA (fetched now):"]
    if preds["vo2max"]:
        fitness_lines.append(f"  VO2 Max:   {preds['vo2max']}")
    if preds["hm"] and preds["source"] == "garmin_race_predictor":
        fitness_lines.append(f"  Predicted HM time:  {preds['hm']}  ← from Garmin Race Predictor")
    elif preds["hm"] and preds["source"] == "vo2max_table_estimate":
        fitness_lines.append(f"  Estimated HM time:  {preds['hm']}  ← interpolated from VO2 Max tables")
    if preds["10k"]:
        fitness_lines.append(f"  Predicted 10k time: {preds['10k']}")
    if preds["5k"]:
        fitness_lines.append(f"  Predicted 5k time:  {preds['5k']}")
    if preds["source"] == "no_data":
        fitness_lines.append("  No Garmin predictions available — will need a target time manually.")

    history = _fetch_running_history_summary()
    if history["has_history"]:
        avg_km   = history["avg_weekly_km"]
        avg_runs = history["avg_runs_per_week"]
        fitness_lines.append(
            f"\n  RECENT TRAINING (last 4 weeks):\n"
            f"    Avg {avg_km} km/week across {avg_runs} runs/week\n"
            f"  [Pass planned_weekly_km={avg_km} if continuing at this level, "
            f"or your actual target. Pass volume_change=continue/increase/decrease/starting_fresh]"
        )
        volume_q = (
            f"  9. Volume plan: you've averaged {avg_km} km/week recently. "
            "Do you plan to continue at this level, increase, or decrease? "
            "If increasing, what's your weekly km target?"
        )
    else:
        fitness_lines.append(
            "\n  RECENT TRAINING: little or no recent running detected."
        )
        volume_q = (
            "  9. Are you starting fresh (little/no current running), or returning from a break? "
            "What weekly km are you aiming to build to? "
            "[Pass volume_change=starting_fresh and your planned_weekly_km target]"
        )

    questions = [
        "\nTO SET UP YOUR TRAINING PLAN, please answer the following:\n",
        "  1. What is your A race?  (name, e.g. 'Belfast Half Marathon')",
        "  2. What is the race date?  (YYYY-MM-DD)",
        "  3. Race distance?  (half_marathon / marathon / 10k / 5k — default: half_marathon)",
        "  4. Do you have a B race (tune-up / fitness check)?  If yes: name + date",
        "  5. How many days per week can you train?  (3–7, counting running + strength)",
        "  6. Include strength training?  (recommended — yes/no, and if yes: how many days/week)",
        f"  7. Target finish time?  (leave blank to use Garmin prediction: {preds['hm'] or 'N/A'})",
        "  8. Which days can you NOT train?  (e.g. 'Monday, Wednesday' — or 'none' if all days are open)",
        volume_q,
    ]

    return (
        "\n".join(fitness_lines) + "\n" + "\n".join(questions) +
        "\n\nThe coach will recommend a training methodology (polarized or pyramidal) "
        "based on your run days and planned volume. "
        "Once you have the answers, call setup_training_plan again with the full arguments."
    )


def create_plan_from_args(arguments: dict, preds: dict) -> tuple[dict, str]:
    race_date_str = arguments["race_date"]
    training_days = arguments["training_days_per_week"]

    race_date    = date.fromisoformat(race_date_str)
    today        = date.today()
    total_weeks  = max(1, (race_date - today).days // 7)
    race_dist    = arguments.get("race_distance", "half_marathon")
    inc_strength = arguments.get("include_strength", True)
    str_days     = arguments.get("strength_days_per_week", 2) if inc_strength else 0
    run_days     = max(1, int(training_days) - str_days)
    target_time  = arguments.get("target_time") or preds.get("hm")

    raw_blocked = arguments.get("blocked_days") or []
    day_names = {"monday","tuesday","wednesday","thursday","friday","saturday","sunday"}
    blocked_days = [
        d.strip().title() for d in raw_blocked
        if d.strip().lower() in day_names
    ]

    planned_weekly_km = arguments.get("planned_weekly_km")
    if planned_weekly_km is not None:
        planned_weekly_km = float(planned_weekly_km)
    volume_change = arguments.get("volume_change")

    explicit_methodology = arguments.get("methodology")
    if explicit_methodology in ("polarized", "pyramidal"):
        methodology  = explicit_methodology
        method_rationale = "Methodology set manually."
    else:
        methodology, method_rationale = _recommend_methodology(run_days, planned_weekly_km, volume_change)

    plan = {
        "created_on":             today.isoformat(),
        "race_name":              arguments.get("race_name", "A Race"),
        "race_date":              race_date_str,
        "race_distance":          race_dist,
        "b_race_name":            arguments.get("b_race_name"),
        "b_race_date":            arguments.get("b_race_date"),
        "total_weeks":            total_weeks,
        "training_days_per_week": int(training_days),
        "run_days_per_week":      run_days,
        "include_strength":       inc_strength,
        "strength_days_per_week": str_days,
        "blocked_days":           blocked_days,
        "methodology":            methodology,
        "planned_weekly_km":      planned_weekly_km,
        "target_time":            target_time,
        "predicted_hm_time":      preds.get("hm"),
        "vo2max_at_setup":        preds.get("vo2max"),
        "prediction_source":      preds.get("source"),
    }

    phase_breakdown = []
    for w in range(1, total_weeks + 1):
        ph = get_plan_phase(w, total_weeks)
        if not phase_breakdown or phase_breakdown[-1][0] != ph:
            phase_start = today + timedelta(weeks=w - 1)
            phase_breakdown.append((ph, w, phase_start))

    lines = [
        f"TRAINING PLAN CREATED — {today.strftime('%d %b %Y')}",
        f"",
        f"  Race:        {plan['race_name']} ({race_dist.replace('_', ' ').title()})",
        f"  Race date:   {race_date.strftime('%d %b %Y')}  ({total_weeks} weeks away)",
        f"  Target time: {target_time or 'not set'}",
        f"  Predicted:   {preds.get('hm') or 'N/A'}  (source: {preds.get('source')})",
        f"",
        f"  Weekly structure:",
        f"    Running days:  {run_days}/week",
        f"    Strength days: {str_days}/week" if inc_strength else "    Strength: not included",
        f"    Blocked days:  {', '.join(blocked_days) if blocked_days else 'none'}",
        f"    Methodology:   {methodology.title()} — {method_rationale}",
        f"",
        f"  PHASE BREAKDOWN:",
    ]
    for ph, start_week, start_date_val in phase_breakdown:
        lines.append(f"    Week {start_week:>2}+  {ph:<12}  (from {start_date_val.strftime('%d %b')})")

    if plan["b_race_name"]:
        lines.append(f"\n  B Race: {plan['b_race_name']} — {plan['b_race_date']}")

    lines.append(f"\nPlan saved to {PLAN_FILE}")
    lines.append("All daily coaching tools will now include plan context automatically.")

    return plan, "\n".join(lines)


def plan_week_sessions(
    plan: dict,
    week_start: date,
    target_km: float,
    acwr: float | None,
) -> list[dict]:
    from coaching.thresholds import get_zones

    today       = date.today()
    race_date   = date.fromisoformat(plan["race_date"])
    total_weeks = plan["total_weeks"]
    created_on  = date.fromisoformat(plan["created_on"])
    weeks_elapsed = (week_start - created_on).days // 7
    current_week  = min(weeks_elapsed + 1, total_weeks)
    phase         = get_plan_phase(current_week, total_weeks)

    run_days  = plan.get("run_days_per_week", 3)
    blocked   = {d.lower() for d in (plan.get("blocked_days") or [])}

    if acwr is not None and acwr > 1.3:
        target_km = round(target_km * 0.80, 1)
    elif acwr is not None and acwr < 0.8 and target_km > 0:
        target_km = round(target_km * 1.10, 1)

    if phase == "Taper":
        target_km = round(target_km * 0.60, 1)
    elif phase == "Race Week":
        target_km = round(target_km * 0.30, 1)

    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    available = [
        week_start + timedelta(days=i)
        for i, d in enumerate(day_names)
        if d not in blocked and (week_start + timedelta(days=i)) >= today
    ]
    run_slots = available[:run_days]

    if not run_slots:
        return []

    try:
        zones = get_zones()
        easy_fast  = zones["easy"]["pace"][0]  if zones["easy"].get("pace") else None
        easy_slow  = zones["easy"]["pace"][1]  if zones["easy"].get("pace") else None
        tempo_fast = zones["threshold"]["pace"][0] if zones["threshold"].get("pace") else None
        tempo_slow = zones["threshold"]["pace"][1] if zones["threshold"].get("pace") else None
        int_fast   = zones["interval"]["pace"][0]  if zones["interval"].get("pace") else None
        int_slow   = zones["interval"]["pace"][1]  if zones["interval"].get("pace") else None
    except Exception:
        easy_fast = easy_slow = tempo_fast = tempo_slow = int_fast = int_slow = None

    sessions: list[dict] = []
    n = len(run_slots)

    long_km   = round(target_km * 0.30, 1)
    other_km  = round((target_km - long_km) / max(n - 1, 1), 1) if n > 1 else target_km

    def _easy_steps(km: float) -> list[dict]:
        dur = round(km * (easy_slow or 360) / 60)
        step: dict = {"type": "easy", "duration_minutes": max(dur, 20)}
        if easy_fast and easy_slow:
            step["target_pace_fast"] = easy_fast
            step["target_pace_slow"] = easy_slow
        return [
            {"type": "warmup",   "duration_minutes": 5},
            step,
            {"type": "cooldown", "duration_minutes": 5},
        ]

    def _tempo_steps(km: float) -> list[dict]:
        tempo_min = max(round(km * 0.6 * (tempo_slow or 300) / 60), 15)
        interval_step: dict = {"type": "interval", "duration_minutes": tempo_min}
        if tempo_fast and tempo_slow:
            interval_step["target_pace_fast"] = tempo_fast
            interval_step["target_pace_slow"] = tempo_slow
        return [
            {"type": "warmup",   "duration_minutes": 10},
            interval_step,
            {"type": "cooldown", "duration_minutes": 10},
        ]

    def _interval_steps() -> list[dict]:
        interval_step: dict = {"type": "interval", "distance_meters": 800}
        recovery_step: dict = {"type": "recovery", "duration_minutes": 2}
        if int_fast and int_slow:
            interval_step["target_pace_fast"] = int_fast
            interval_step["target_pace_slow"] = int_slow
        return [
            {"type": "warmup", "duration_minutes": 10},
            {"type": "repeat", "iterations": 5, "steps": [interval_step, recovery_step]},
            {"type": "cooldown", "duration_minutes": 10},
        ]

    for i, slot_date in enumerate(run_slots):
        is_last_slot = (i == n - 1)

        if phase in ("Base", "Taper", "Race Week"):
            km = long_km if is_last_slot and n > 1 else other_km
            sessions.append({
                "date":                slot_date.isoformat(),
                "type":                "long_run" if is_last_slot and n > 1 else "easy_run",
                "workout_name":        f"{'Long Run' if is_last_slot and n > 1 else 'Easy Run'} — {phase} Wk{current_week}",
                "description":         "Easy aerobic run. Stay in Z1-2 throughout.",
                "planned_distance_km": km,
                "steps":               _easy_steps(km),
            })

        elif phase == "Development":
            if i == 1 and n >= 3:
                km = other_km
                sessions.append({
                    "date":                slot_date.isoformat(),
                    "type":                "tempo",
                    "workout_name":        f"Tempo Run — Development Wk{current_week}",
                    "description":         "Threshold effort. Comfortably hard — could speak in single words.",
                    "planned_distance_km": km,
                    "steps":               _tempo_steps(km),
                })
            else:
                km = long_km if is_last_slot and n > 1 else other_km
                sessions.append({
                    "date":                slot_date.isoformat(),
                    "type":                "long_run" if is_last_slot and n > 1 else "easy_run",
                    "workout_name":        f"{'Long Run' if is_last_slot and n > 1 else 'Easy Run'} — Development Wk{current_week}",
                    "description":         "Easy aerobic run.",
                    "planned_distance_km": km,
                    "steps":               _easy_steps(km),
                })

        elif phase == "Specific":
            if i == 1 and n >= 3:
                sessions.append({
                    "date":                slot_date.isoformat(),
                    "type":                "interval",
                    "workout_name":        f"Intervals — Specific Wk{current_week}",
                    "description":         "5×800m at hard effort (5k–10k race pace). Full recovery between reps.",
                    "planned_distance_km": round(other_km, 1),
                    "steps":               _interval_steps(),
                })
            elif i == n - 2 and n >= 4:
                km = other_km
                sessions.append({
                    "date":                slot_date.isoformat(),
                    "type":                "tempo",
                    "workout_name":        f"Tempo Run — Specific Wk{current_week}",
                    "description":         "Threshold effort tempo run.",
                    "planned_distance_km": km,
                    "steps":               _tempo_steps(km),
                })
            else:
                km = long_km if is_last_slot and n > 1 else other_km
                sessions.append({
                    "date":                slot_date.isoformat(),
                    "type":                "long_run" if is_last_slot and n > 1 else "easy_run",
                    "workout_name":        f"{'Long Run' if is_last_slot and n > 1 else 'Easy Run'} — Specific Wk{current_week}",
                    "description":         "Easy aerobic run.",
                    "planned_distance_km": km,
                    "steps":               _easy_steps(km),
                })

    return sessions


def format_plan_status(plan: dict) -> str:
    today         = date.today()
    race_date     = date.fromisoformat(plan["race_date"])
    created_on    = date.fromisoformat(plan["created_on"])
    days_to_race  = (race_date - today).days
    total_weeks   = plan["total_weeks"]
    weeks_elapsed = (today - created_on).days // 7
    current_week  = min(weeks_elapsed + 1, total_weeks)
    phase         = get_plan_phase(current_week, total_weeks)

    countdown = (
        f"{abs(days_to_race)}d ago" if days_to_race < 0
        else "TODAY" if days_to_race == 0
        else f"{days_to_race // 7}w {days_to_race % 7}d"
    )

    lines = [
        f"TRAINING PLAN STATUS — {today.strftime('%A %d %B %Y')}",
        f"",
        f"  Race:          {plan['race_name']} ({plan['race_distance'].replace('_', ' ').title()})",
        f"  Race date:     {race_date.strftime('%d %b %Y')}  ({countdown})",
        f"  Target time:   {plan.get('target_time') or 'not set'}",
        f"  Predicted:     {plan.get('predicted_hm_time') or 'N/A'}  "
        f"(source: {plan.get('prediction_source', 'N/A')})",
        f"  VO2 Max:       {plan.get('vo2max_at_setup') or 'N/A'}  (at plan creation)",
        f"",
        f"  CURRENT POSITION:",
        f"    Week {current_week} of {total_weeks}  |  Phase: {phase}",
    ]

    phase_tips = {
        "Base":        "Focus entirely on easy aerobic runs. HR zone 1–2. Build consistency.",
        "Development": "Add one quality session (20min tempo or 4×8min threshold). 80% still easy.",
        "Specific":    "Race-pace intervals and longer tempo blocks. This is where time improvements happen.",
        "Taper":       "Volume drops 40–50%. Keep one short quality session. Trust the process.",
        "Race Week":   "Shake-out runs only. Race day execution is the priority now.",
    }
    lines.append(f"    → {phase_tips.get(phase, '')}")
    lines.append(f"")

    lines.append(f"  WEEKLY STRUCTURE:")
    lines.append(f"    Running days:  {plan.get('run_days_per_week', 'N/A')}/week")
    if plan.get("include_strength"):
        lines.append(f"    Strength days: {plan.get('strength_days_per_week', 0)}/week")
    blocked = plan.get("blocked_days") or []
    lines.append(f"    Blocked days:  {', '.join(blocked) if blocked else 'none'}")
    lines.append(f"    Methodology:   {plan.get('methodology', 'polarized').title()}")

    if plan.get("b_race_name") and plan.get("b_race_date"):
        b_date   = date.fromisoformat(plan["b_race_date"])
        b_days   = (b_date - today).days
        b_status = (
            f"{abs(b_days)}d ago" if b_days < 0
            else "TODAY" if b_days == 0
            else f"in {b_days // 7}w {b_days % 7}d"
        )
        lines.append(f"\n  B Race: {plan['b_race_name']} — {b_date.strftime('%d %b %Y')} ({b_status})")

    lines.append(f"\n  PHASE BREAKDOWN (full plan):")
    prev_phase = None
    phase_start_week = 1
    for w in range(1, total_weeks + 2):
        ph = get_plan_phase(w, total_weeks) if w <= total_weeks else None
        if ph != prev_phase and prev_phase is not None:
            marker = "◀ NOW" if phase_start_week <= current_week < w else ""
            lines.append(f"    Weeks {phase_start_week:>2}–{w-1:<2}  {prev_phase:<12}  {marker}")
            phase_start_week = w
        prev_phase = ph

    lines.append(f"\n  Plan created: {plan['created_on']}  |  File: {PLAN_FILE}")
    return "\n".join(lines)
