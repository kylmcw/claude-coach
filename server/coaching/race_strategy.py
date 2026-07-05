from datetime import date, timedelta

from coaching.plan import load_plan, get_plan_phase


def _parse_time(t: str) -> int | None:
    """Parse H:MM:SS or M:SS into total seconds. Returns None on failure."""
    try:
        parts = [int(x) for x in t.strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
    except Exception:
        pass
    return None


def _fmt_time(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace(secs_per_km: float) -> str:
    m, s = divmod(int(secs_per_km), 60)
    return f"{m}:{s:02d}/km"


_RACE_DISTANCES_KM = {
    "5k":            5.0,
    "10k":           10.0,
    "half_marathon": 21.0975,
    "marathon":      42.195,
}

# Positive-split guard: first km bands as % of goal pace
_FIRST_KM_FACTOR = 1.04   # start 4% slower than goal pace


def build_race_strategy(
    plan: dict | None,
    target_time_override: str | None = None,
    predicted_time: str | None = None,
) -> str:
    """
    Generate a race-day pacing strategy from the active training plan.
    `target_time_override` allows an ad-hoc target without altering the plan.
    `predicted_time` is Garmin's current race prediction (for gap analysis).
    """
    today = date.today()

    if not plan:
        return (
            "NO TRAINING PLAN FOUND.\n"
            "Call setup_training_plan to create a plan first, then use get_race_strategy."
        )

    race_name  = plan.get("race_name", "Race")
    race_date  = date.fromisoformat(plan["race_date"])
    race_dist  = plan.get("race_distance", "half_marathon")
    dist_km    = _RACE_DISTANCES_KM.get(race_dist, 21.0975)
    days_out   = (race_date - today).days

    # ── Target time resolution ─────────────────────────────────────────────
    target_str = target_time_override or plan.get("target_time")
    target_secs = _parse_time(target_str) if target_str else None
    pred_secs   = _parse_time(predicted_time) if predicted_time else None

    # Current training phase + taper status
    total_weeks   = plan["total_weeks"]
    created_on    = date.fromisoformat(plan["created_on"])
    weeks_elapsed = (today - created_on).days // 7
    current_week  = min(weeks_elapsed + 1, total_weeks)
    phase         = get_plan_phase(current_week, total_weeks)

    lines = [
        f"RACE STRATEGY — {race_name}",
        f"  Distance:   {race_dist.replace('_', ' ').title()} ({dist_km} km)",
        f"  Race date:  {race_date.strftime('%d %b %Y')}  "
        + ("(TODAY)" if days_out == 0 else
           f"({days_out} days / {days_out // 7}w {days_out % 7}d away)"),
        f"  Phase:      Week {current_week}/{total_weeks} — {phase}",
        "",
    ]

    # ── Gap analysis ───────────────────────────────────────────────────────
    if target_secs and pred_secs:
        gap_secs = target_secs - pred_secs
        if abs(gap_secs) <= 60:
            lines.append(f"  Target {_fmt_time(target_secs)} vs Garmin prediction {_fmt_time(pred_secs)} — on track.")
        elif gap_secs < 0:
            # Target is faster than prediction — ambitious
            lines.append(
                f"  ⚠ Target {_fmt_time(target_secs)} is {_fmt_time(abs(gap_secs))} faster "
                f"than Garmin's current prediction ({_fmt_time(pred_secs)}).\n"
                f"  This is achievable but will require executing taper perfectly and racing smart."
            )
        else:
            lines.append(
                f"  ✓ Target {_fmt_time(target_secs)} is {_fmt_time(gap_secs)} more conservative "
                f"than prediction ({_fmt_time(pred_secs)}) — solid buffer."
            )
        lines.append("")
    elif target_secs:
        lines.append(f"  Target: {_fmt_time(target_secs)}")
        lines.append("")

    if not target_secs:
        lines.append(
            "  No target time set. Call setup_training_plan with a target_time, "
            "or pass target_time to this tool."
        )
        return "\n".join(lines)

    # ── Goal pace ─────────────────────────────────────────────────────────
    goal_pace_sec = target_secs / dist_km
    lines.append(f"GOAL PACE: {_fmt_pace(goal_pace_sec)}/km  (target: {_fmt_time(target_secs)})")
    lines.append("")

    # ── Pacing bands per km ───────────────────────────────────────────────
    lines.append("PER-KM PACING PLAN:")
    lines.append(
        f"  {'KM':<6} {'Target pace':>12}  Notes"
    )
    lines.append(f"  {'—'*6} {'—'*12}  {'—'*30}")

    km_markers = list(range(1, int(dist_km) + 1))
    # Add fractional finish only when it's meaningfully different from the last int marker
    if dist_km % 1 > 0.05 and dist_km - int(dist_km) > 0.05:
        # Replace the last integer if the finish is within 0.15 km of it
        if dist_km - int(dist_km) < 0.15:
            km_markers[-1] = dist_km
        else:
            km_markers.append(dist_km)

    for km in km_markers:
        frac = km / dist_km
        is_final = (km == km_markers[-1])

        if frac <= 0.10:
            # First 10%: hold back 3-4% (prevent blowup)
            pace = goal_pace_sec * 1.035
            note = "Hold back — start conservatively"
        elif frac <= 0.20:
            pace = goal_pace_sec * 1.015
            note = "Settle into rhythm"
        elif frac <= 0.70:
            pace = goal_pace_sec
            note = "Lock onto goal pace"
        elif frac <= 0.85:
            # Entering the hard section — slight push
            pace = goal_pace_sec * 0.995
            note = "Stay composed — it's meant to hurt here"
        else:
            # Final 15%: leave everything on the course
            pace = goal_pace_sec * (0.985 if frac > 0.95 else 0.990)
            note = "Empty the tank" if frac > 0.90 else "Controlled push"

        km_label = f"{km:.1f}" if isinstance(km, float) and km != int(km) else str(int(km))
        lines.append(f"  {km_label:<6} {_fmt_pace(pace):>12}  {note}")

    lines.append("")

    # ── Splits at 25/50/75% ───────────────────────────────────────────────
    half_target = round(target_secs * 0.49)  # slight negative split: first half 49%
    lines.append("SPLIT TARGETS:")
    lines.append(f"  First half:  {_fmt_time(half_target)}  (go out slightly conservative)")
    lines.append(f"  Second half: {_fmt_time(target_secs - half_target)}  (negative split or even)")
    lines.append("")

    # ── Taper check ───────────────────────────────────────────────────────
    lines.append("TAPER & RACE-DAY CHECKLIST:")
    if days_out > 14:
        lines.append(f"  • {days_out // 7}w {days_out % 7}d out — focus on completing your {phase} block before taper.")
    elif days_out > 7:
        lines.append("  • Final week of taper. Cut volume 40–50%. Keep one short quality session (20min at race pace).")
        lines.append("  • Prioritise sleep and carb loading in the last 3 days.")
    elif days_out > 2:
        lines.append("  • Race week. Easy shakeout runs only (20–30 min). Legs stay fresh.")
        lines.append("  • Carb load 48h out. Hydrate consistently.")
    elif days_out == 1:
        lines.append("  • Race tomorrow. 20–30 min easy shakeout with 4–6 strides. Rest.")
        lines.append("  • Lay out kit, pin bib, confirm start time and warm-up plan.")
    elif days_out == 0:
        lines.append("  • RACE DAY.")
        lines.append(f"  • Warm up 15–20 min easy + 4–6 strides. Aim for {_fmt_pace(goal_pace_sec * 1.035)}/km first km.")

    lines.append("")
    lines.append("RACE-DAY PACING CUES:")
    lines.append(f"  • Km 1–2: Feel easy. If it feels too slow, it's correct.")
    lines.append(f"  • Middle third: Controlled effort. HR should be threshold, not maximal.")
    lines.append(f"  • Final 3km: Commit. Pain is the goal pace working.")

    return "\n".join(lines)
