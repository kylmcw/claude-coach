import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from garminconnect import Garmin
from garminconnect.workout import (
    BaseWorkout,
    RunningWorkout,
    FitnessEquipmentWorkout,
    WorkoutSegment,
    ExecutableStep,
    RepeatGroup,
    StepType,
    ConditionType,
    TargetType,
    create_warmup_step,
    create_cooldown_step,
    create_interval_step,
    create_recovery_step,
    create_repeat_group,
)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("garmin-coach")

# ─── Fallback baselines (used only when calibration has no data) ─────────────
# These are population-average-ish values. In practice, load_baselines() will
# compute personal values from the user's own Garmin history on first run.
HRV_LOW_DEFAULT  = 50
HRV_HIGH_DEFAULT = 70
RHR_NORM_DEFAULT = 60

# ─── Garmin workout end-condition IDs ──────────────────────────────────────────
# Garmin keys workout steps off the numeric conditionTypeId, NOT the string key —
# if the id and key disagree, the id wins. The garminconnect library's
# ConditionType enum has the WRONG numbers (it defines DISTANCE=1), which
# silently turns distance laps into "press lap button" steps (id 1 = lap.button).
# These constants are Garmin's real values; use them instead of the library enum.
COND_TIME     = 2
COND_DISTANCE = 3

# ─── Calibration config ───────────────────────────────────────────────────────
CALIBRATION_FILE        = Path.home() / ".garmin-coach.json"
RECALIBRATE_AFTER_DAYS  = 7   # silently recalibrate if baseline is older than this
CALIBRATION_LOOKBACK    = 30  # days of history to sample for baselines

# ─── Training plan config ─────────────────────────────────────────────────────
PLAN_FILE = Path.home() / ".garmin-coach-plan.json"

# VO2 Max → predicted half-marathon time lookup table.
# Values derived from Daniels' VDOT tables (running @ ~82% VO2Max for HM).
# Interpolation is linear between entries.
_VO2MAX_HM_TABLE = [
    (35, "2:29:00"),
    (38, "2:18:00"),
    (40, "2:10:00"),
    (42, "2:04:00"),
    (44, "1:57:00"),
    (46, "1:51:00"),
    (48, "1:46:00"),
    (50, "1:41:00"),
    (52, "1:37:00"),
    (54, "1:33:00"),
    (56, "1:29:00"),
    (58, "1:25:00"),
    (60, "1:22:00"),
    (63, "1:17:00"),
    (66, "1:13:00"),
    (70, "1:07:00"),
]


def _hms_to_seconds(t: str) -> int:
    """'H:MM:SS' or 'MM:SS' → total seconds."""
    parts = t.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])


def _seconds_to_hms(s: int) -> str:
    """Total seconds → 'H:MM:SS'."""
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h}:{m:02d}:{sec:02d}"


def vo2max_to_hm_prediction(vo2max: float) -> tuple[str, str]:
    """
    Return (predicted_hm_time, pace_per_km) for a given VO2 Max.
    Uses linear interpolation across the VDOT lookup table.
    pace_per_km is formatted as 'M:SS/km'.
    """
    table = _VO2MAX_HM_TABLE
    if vo2max <= table[0][0]:
        hm_secs = _hms_to_seconds(table[0][1])
    elif vo2max >= table[-1][0]:
        hm_secs = _hms_to_seconds(table[-1][1])
    else:
        for i in range(len(table) - 1):
            v0, t0 = table[i][0], _hms_to_seconds(table[i][1])
            v1, t1 = table[i + 1][0], _hms_to_seconds(table[i + 1][1])
            if v0 <= vo2max <= v1:
                ratio   = (vo2max - v0) / (v1 - v0)
                hm_secs = round(t0 + ratio * (t1 - t0))
                break
        else:
            hm_secs = _hms_to_seconds(table[-1][1])

    pace_secs_per_km = round(hm_secs / 21.0975)
    pace_str = f"{pace_secs_per_km // 60}:{pace_secs_per_km % 60:02d}/km"
    return _seconds_to_hms(hm_secs), pace_str


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
    """
    Return the training phase name for a given week position.
    Phases scaled to plan length:
      - First 25%:    Base
      - 25–55%:       Development
      - 55–80%:       Specific
      - 80–92%:       Taper
      - Last 8%:      Race week
    """
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


_NO_PLAN_NUDGE = (
    "\n\n💡 No training plan set up. "
    "Call setup_training_plan (no arguments needed) to get started — "
    "the coach will pull your Garmin race predictions and walk you through the questions."
)


def format_plan_context(plan: dict) -> str:
    """
    Build a compact PLAN CONTEXT block to append to daily tool responses.
    Shows race, current week/phase, and days remaining.
    """
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

# ─── Calibration ─────────────────────────────────────────────────────────────

def calibrate_baselines() -> tuple[int, int, int]:
    """
    Pull the last CALIBRATION_LOOKBACK days of HRV and RHR from Garmin,
    compute personal baselines, persist them to CALIBRATION_FILE, and return
    (hrv_low, hrv_high, rhr_norm).

    HRV band  = [mean − stddev, mean + stddev], clamped to a minimum width of 10.
    RHR norm  = 30-day mean, rounded to nearest integer.

    Falls back to *_DEFAULT constants for any metric with insufficient data.
    """
    client     = get_client()
    hrv_values: list[float] = []
    rhr_values: list[int]   = []

    for days_ago in range(1, CALIBRATION_LOOKBACK + 1):
        check_date = (date.today() - timedelta(days=days_ago)).isoformat()

        try:
            hrv_data = client.get_hrv_data(check_date)
            if hrv_data and "hrvSummary" in hrv_data:
                val = hrv_data["hrvSummary"].get("lastNightAvg")
                if val and val > 0:
                    hrv_values.append(float(val))
        except Exception:
            pass

        try:
            hr_data = client.get_heart_rates(check_date)
            rhr = hr_data.get("restingHeartRate")
            if rhr and rhr > 0:
                rhr_values.append(int(rhr))
        except Exception:
            pass

    # ── HRV baseline ──────────────────────────────────────────────────────────
    if hrv_values:
        hrv_mean = sum(hrv_values) / len(hrv_values)
        variance = sum((x - hrv_mean) ** 2 for x in hrv_values) / len(hrv_values)
        hrv_std  = variance ** 0.5
        spread   = max(hrv_std, 5)          # minimum ±5 band
        hrv_low  = max(1, round(hrv_mean - spread))
        hrv_high = round(hrv_mean + spread)
    else:
        hrv_mean, hrv_low, hrv_high = None, HRV_LOW_DEFAULT, HRV_HIGH_DEFAULT

    # ── RHR baseline ──────────────────────────────────────────────────────────
    if rhr_values:
        rhr_mean = sum(rhr_values) / len(rhr_values)
        rhr_norm = round(rhr_mean)
    else:
        rhr_mean, rhr_norm = None, RHR_NORM_DEFAULT

    state = {
        "calibrated_on":  date.today().isoformat(),
        "hrv_low":        hrv_low,
        "hrv_high":       hrv_high,
        "rhr_norm":       rhr_norm,
        "hrv_mean":       round(hrv_mean, 1) if hrv_mean is not None else None,
        "rhr_mean":       round(rhr_mean, 1) if rhr_mean is not None else None,
        "hrv_samples":    len(hrv_values),
        "rhr_samples":    len(rhr_values),
        "lookback_days":  CALIBRATION_LOOKBACK,
    }
    CALIBRATION_FILE.write_text(json.dumps(state, indent=2))
    return hrv_low, hrv_high, rhr_norm


def load_baselines() -> tuple[int, int, int]:
    """
    Return (hrv_low, hrv_high, rhr_norm) for the current user.

    - No state file → first run: calibrate now.
    - State file present but older than RECALIBRATE_AFTER_DAYS → recalibrate silently.
    - State file present and fresh → use stored values.
    - Any read/parse error → recalibrate.
    """
    if CALIBRATION_FILE.exists():
        try:
            state          = json.loads(CALIBRATION_FILE.read_text())
            calibrated_on  = date.fromisoformat(state["calibrated_on"])
            days_since     = (date.today() - calibrated_on).days
            if days_since < RECALIBRATE_AFTER_DAYS:
                return state["hrv_low"], state["hrv_high"], state["rhr_norm"]
        except Exception:
            pass  # fall through to recalibration

    return calibrate_baselines()


# ─── Cached client (reused across tool calls in the same server session) ─────
_client = None

def get_client() -> Garmin:
    global _client
    if _client is None:
        _client = Garmin(
            os.environ["GARMIN_EMAIL"],
            os.environ["GARMIN_PASSWORD"]
        )
        _client.login()
    return _client


# ─── Data fetchers ────────────────────────────────────────────────────────────

def fetch_morning_data():
    """HRV, RHR, sleep, body battery, stress — for daily readiness."""
    client    = get_client()
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    hr_data     = client.get_heart_rates(today)
    hrv_data    = client.get_hrv_data(today)
    sleep_data  = client.get_sleep_data(today)
    bb_data     = client.get_body_battery(yesterday, today)
    stress_data = client.get_stress_data(yesterday)

    # RHR
    resting_hr = hr_data.get("restingHeartRate")

    # HRV
    hrv_nightly = None
    if hrv_data and "hrvSummary" in hrv_data:
        hrv_nightly = hrv_data["hrvSummary"].get("lastNightAvg")

    # Sleep — use window timestamps (start→end) to match what Garmin app shows.
    # Falls back to stage sum (sleepTimeSeconds + awakeSleepSeconds) if timestamps
    # are absent (older devices / firmware).
    sleep_hours = None
    if sleep_data and "dailySleepDTO" in sleep_data:
        dto   = sleep_data["dailySleepDTO"]
        start = dto.get("sleepStartTimestampGMT")
        end   = dto.get("sleepEndTimestampGMT")
        if start and end:
            sleep_hours = round((end - start) / 3_600_000, 1)  # ms → hours
        else:
            secs = (dto.get("sleepTimeSeconds") or 0) + (dto.get("awakeSleepSeconds") or 0)
            if secs:
                sleep_hours = round(secs / 3600, 1)

    # Body Battery — peak charged value from yesterday/today window
    body_battery = None
    if bb_data:
        for entry in bb_data:
            if isinstance(entry, dict) and "bodyBatteryValuesArray" in entry:
                vals = [v[1] for v in entry["bodyBatteryValuesArray"] if v[1] is not None]
                if vals:
                    body_battery = max(vals)

    # Stress — daily average (ignoring -1 rest/unmeasured periods)
    avg_stress = None
    if stress_data and "allStressData" in stress_data:
        raw = stress_data["allStressData"].get("stressValuesArray", [])
        levels = [s["stressLevel"] for s in raw if s.get("stressLevel", -1) > 0]
        if levels:
            avg_stress = round(sum(levels) / len(levels))

    return {
        "hrv":          hrv_nightly,
        "resting_hr":   resting_hr,
        "sleep_hours":  sleep_hours,
        "body_battery": body_battery,
        "avg_stress":   avg_stress,
    }


def fetch_training_load():
    """Training status + ACWR computed from last 28 days of activities."""
    client = get_client()
    today  = date.today().isoformat()

    training_status = client.get_training_status(today)
    activities      = client.get_activities(0, 30)

    cutoff_28 = date.today() - timedelta(days=28)
    cutoff_7  = date.today() - timedelta(days=7)

    loads_28, loads_7 = [], []

    for act in activities:
        act_date_str = act.get("startTimeLocal", "")[:10]
        try:
            act_date = date.fromisoformat(act_date_str)
        except ValueError:
            continue
        if act_date < cutoff_28:
            continue

        # Aerobic Training Effect (1–5) is the best single-session load proxy
        # Fall back to distance in km if TE isn't available
        load = act.get("aerobicTrainingEffect") or 0
        if load == 0:
            load = round((act.get("distance") or 0) / 1000, 1)

        loads_28.append(load)
        if act_date >= cutoff_7:
            loads_7.append(load)

    acute   = round(sum(loads_7), 1)
    chronic = round(sum(loads_28) / 4, 1) if loads_28 else 0  # 28d total → weekly avg
    acwr    = round(acute / chronic, 2) if chronic > 0 else None

    status_label  = None
    training_load = None
    if training_status:
        status_label  = training_status.get("trainingStatusPhase")
        training_load = training_status.get("trainingLoad")

    return {
        "training_status":    status_label,
        "training_load_7d":   training_load,
        "acute_load":         acute,
        "chronic_load":       chronic,
        "acwr":               acwr,
        "sessions_last_7d":   len(loads_7),
        "sessions_last_28d":  len(loads_28),
    }


def fetch_fitness_trend():
    """VO2 Max sampled weekly over the past 6 weeks."""
    client = get_client()
    trend  = []

    for weeks_ago in range(6, -1, -1):
        check_date = (date.today() - timedelta(weeks=weeks_ago)).isoformat()
        try:
            metrics = client.get_max_metrics(check_date)
            vo2 = None
            if metrics and isinstance(metrics, list):
                for m in metrics:
                    generic = m.get("generic") or {}
                    vo2_val = generic.get("vo2MaxPreciseValue")
                    if vo2_val:
                        vo2 = round(vo2_val, 1)
                        break
            trend.append({"date": check_date, "vo2max": vo2})
        except Exception:
            trend.append({"date": check_date, "vo2max": None})

    return trend


def fetch_recent_activities():
    """Last 7 days of activities with key running metrics."""
    client  = get_client()
    acts    = client.get_activities(0, 15)
    cutoff  = date.today() - timedelta(days=7)
    recent  = []

    for act in acts:
        act_date_str = act.get("startTimeLocal", "")[:10]
        try:
            act_date = date.fromisoformat(act_date_str)
        except ValueError:
            continue
        if act_date < cutoff:
            continue

        distance_km  = round((act.get("distance") or 0) / 1000, 2)
        duration_min = round((act.get("duration") or 0) / 60, 1)
        avg_hr       = act.get("averageHR")

        # Pace in min:sec per km
        if distance_km > 0 and duration_min > 0:
            pace_sec = round(duration_min * 60 / distance_km)
            avg_pace = f"{pace_sec // 60}:{pace_sec % 60:02d}/km"
        else:
            avg_pace = "N/A"

        recent.append({
            "date":         act_date_str,
            "name":         act.get("activityName", "Activity"),
            "type":         act.get("activityType", {}).get("typeKey", "unknown"),
            "distance_km":  distance_km,
            "duration_min": duration_min,
            "avg_hr":       avg_hr,
            "avg_pace":     avg_pace,
            "aerobic_te":   act.get("aerobicTrainingEffect"),
            "anaerobic_te": act.get("anaerobicTrainingEffect"),
        })

    return recent


# ─── Run analysis fetcher ────────────────────────────────────────────────────

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
        summary = client.get_activity(activity_id)
    except Exception as exc:
        return {"error": f"Could not fetch activity {activity_id}: {exc}"}

    # ── Fetch splits ──────────────────────────────────────────────────────
    splits = None
    try:
        splits = client.get_activity_splits(activity_id)
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

    aerobic_te = summary.get("aerobicTrainingEffect")
    anaerobic_te = summary.get("anaerobicTrainingEffect")

    # ── Extract running dynamics ──────────────────────────────────────────
    avg_cadence = summary.get("averageRunningCadenceInStepsPerMinute")
    max_cadence = summary.get("maxRunningCadenceInStepsPerMinute")
    avg_stride_length = summary.get("avgStrideLength")          # metres (usually)
    avg_ground_contact_time = summary.get("avgGroundContactTime")  # ms
    avg_ground_contact_balance = summary.get("avgGroundContactBalance")  # % (left)
    avg_vertical_oscillation = summary.get("avgVerticalOscillation")  # cm
    avg_vertical_ratio = summary.get("avgVerticalRatio")        # %
    avg_power = summary.get("avgPower")                          # watts
    max_power = summary.get("maxPower")
    normalized_power = summary.get("normPower")
    training_stress_score = summary.get("trainingStressScore")

    # Some devices store stride length in cm, normalise to metres
    if avg_stride_length and avg_stride_length > 10:
        avg_stride_length = round(avg_stride_length / 100, 2)  # cm → m
    elif avg_stride_length:
        avg_stride_length = round(avg_stride_length, 2)

    # ── Parse splits ──────────────────────────────────────────────────────
    parsed_splits = []
    if splits:
        split_list = splits.get("lapDTOs") or splits.get("splitSummaries") or []
        if not split_list:
            # Try alternative structure
            split_list = splits if isinstance(splits, list) else []
        for i, s in enumerate(split_list):
            s_dist = round((s.get("distance") or 0) / 1000, 2)
            s_dur = s.get("duration") or s.get("elapsedDuration") or 0
            s_dur_sec = s_dur if s_dur < 10000 else s_dur / 1000  # handle ms vs s
            if s_dist > 0 and s_dur_sec > 0:
                s_pace_sec = round(s_dur_sec / s_dist)
                s_pace = f"{s_pace_sec // 60}:{s_pace_sec % 60:02d}/km"
            else:
                s_pace = "N/A"
            parsed_splits.append({
                "split": i + 1,
                "distance_km": s_dist,
                "duration_sec": round(s_dur_sec, 1),
                "pace": s_pace,
                "avg_hr": s.get("averageHR"),
                "max_hr": s.get("maxHR"),
                "avg_cadence": s.get("averageRunningCadenceInStepsPerMinute"),
                "elevation_gain": s.get("elevationGain"),
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
        "activity_name": summary.get("activityName", "Activity"),
        "activity_type": summary.get("activityType", {}).get("typeKey", "unknown"),
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


# ─── Weekly / monthly review fetchers ────────────────────────────────────────

def _week_bounds(weeks_back: int) -> tuple[date, date]:
    """Return (monday, sunday) for a given number of weeks ago (0 = current week)."""
    today = date.today()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_back)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _month_bounds(months_back: int) -> tuple[date, date]:
    """Return (first_day, last_day) for a given number of months ago (0 = current month)."""
    import calendar
    today = date.today()
    year  = today.year
    month = today.month - months_back
    while month <= 0:
        month += 12
        year  -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _summarise_activities(activities: list[dict], start: date, end: date) -> dict:
    """
    Filter a raw Garmin activity list to [start, end] inclusive and compute
    totals useful for weekly/monthly reviews.
    """
    window = []
    for act in activities:
        act_date_str = act.get("startTimeLocal", "")[:10]
        try:
            act_date = date.fromisoformat(act_date_str)
        except ValueError:
            continue
        if start <= act_date <= end:
            window.append(act)

    total_distance_km = round(sum((a.get("distance") or 0) for a in window) / 1000, 2)
    total_duration_min = round(sum((a.get("duration") or 0) for a in window) / 60, 1)
    session_count = len(window)

    # Activity type breakdown
    type_counts: dict[str, int] = {}
    for a in window:
        t = a.get("activityType", {}).get("typeKey", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # Best session by aerobic training effect
    best = None
    best_te = -1.0
    for a in window:
        te = a.get("aerobicTrainingEffect") or 0
        if te > best_te:
            best_te = te
            best = a

    best_session = None
    if best:
        dist_km = round((best.get("distance") or 0) / 1000, 2)
        dur_min = round((best.get("duration") or 0) / 60, 1)
        best_session = {
            "date":         best.get("startTimeLocal", "")[:10],
            "name":         best.get("activityName", "Activity"),
            "type":         best.get("activityType", {}).get("typeKey", "unknown"),
            "distance_km":  dist_km,
            "duration_min": dur_min,
            "aerobic_te":   best_te,
        }

    # Running-only stats for pace context
    run_acts = [
        a for a in window
        if (a.get("activityType", {}).get("typeKey") or "").startswith("running")
        or (a.get("activityType", {}).get("typeKey") or "") == "trail_running"
    ]
    run_distance_km  = round(sum((a.get("distance") or 0) for a in run_acts) / 1000, 2)
    longest_run_km   = round(max(((a.get("distance") or 0) for a in run_acts), default=0) / 1000, 2)

    return {
        "start":             start.isoformat(),
        "end":               end.isoformat(),
        "session_count":     session_count,
        "total_distance_km": total_distance_km,
        "total_duration_min": total_duration_min,
        "run_distance_km":   run_distance_km,
        "longest_run_km":    longest_run_km,
        "type_counts":       type_counts,
        "best_session":      best_session,
    }


def fetch_weekly_summary(weeks_back: int = 0) -> dict:
    """Summarise activity data for a calendar week (Mon–Sun), N weeks ago."""
    client = get_client()
    acts   = client.get_activities(0, 40)  # enough to cover 2 full weeks
    start, end = _week_bounds(weeks_back)
    summary = _summarise_activities(acts, start, min(end, date.today()))
    summary["week_label"] = (
        "This week" if weeks_back == 0
        else "Last week" if weeks_back == 1
        else f"{weeks_back} weeks ago"
    )
    return summary


def fetch_monthly_summary(months_back: int = 0) -> dict:
    """Summarise activity data for a calendar month, N months ago."""
    client = get_client()
    # Pull enough activities to cover the target month + current month
    acts   = client.get_activities(0, 60)
    start, end = _month_bounds(months_back)
    summary = _summarise_activities(acts, start, min(end, date.today()))

    # Weekly consistency: count weeks within the month that had >= 3 sessions
    consistent_weeks = 0
    total_weeks = 0
    check = start
    while check <= end:
        w_start = check
        w_end   = min(check + timedelta(days=6), end)
        w_data  = _summarise_activities(acts, w_start, min(w_end, date.today()))
        if w_data["session_count"] > 0:  # only count weeks that have started
            total_weeks += 1
            if w_data["session_count"] >= 3:
                consistent_weeks += 1
        check += timedelta(weeks=1)

    summary["month_label"]       = (
        start.strftime("%B %Y") if months_back > 0
        else f"{start.strftime('%B %Y')} (to date)"
    )
    summary["consistent_weeks"]  = consistent_weeks
    summary["total_weeks"]       = total_weeks
    return summary


def _week_recommendation(acwr, vol_delta_pct: float | None) -> str:
    """Plain-English recommendation for the coming week based on ACWR and volume delta."""
    if acwr is None:
        return "Not enough data for a load recommendation — build gradually."
    if acwr > 1.5:
        return "MANDATORY EASY WEEK. ACWR in danger zone — cut volume 30–40%, no intensity."
    if acwr > 1.3:
        return "Back off. ACWR elevated — reduce volume ~20% and keep effort easy."
    if acwr < 0.8:
        if vol_delta_pct is not None and vol_delta_pct < -20:
            return "Volume dropped significantly. Safe to rebuild — add ~10% this week."
        return "Underloading. Room to add ~10% volume or one extra easy session."
    # Optimal range (0.8–1.3)
    if vol_delta_pct is not None and vol_delta_pct > 15:
        return "Big volume jump this week. Hold steady next week before building again."
    return "Load is optimal. Stay the course — execute your next planned block."


# ─── Weather helpers ─────────────────────────────────────────────────────────

def resolve_location(location: str | None) -> tuple[float, float, str]:
    """
    Return (latitude, longitude, place_label) for the run window call.

    If `location` is a non-empty string, geocode it via the Open-Meteo
    geocoding API (free, no key).  Otherwise fall back to IP geolocation
    via ip-api.com (also free, no key) which auto-detects the caller's
    approximate position — accurate to city level (~1–5 km).
    """
    if location:
        encoded = urllib.parse.quote(location)
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            f"?name={encoded}&count=1&language=en&format=json"
        )
        with urllib.request.urlopen(geo_url, timeout=10) as resp:
            geo = json.loads(resp.read())
        results = geo.get("results")
        if not results:
            raise ValueError(f"Could not geocode location: '{location}'")
        r = results[0]
        label = r.get("name", location)
        country = r.get("country", "")
        if country:
            label = f"{label}, {country}"
        return float(r["latitude"]), float(r["longitude"]), label

    # Auto-detect via IP geolocation
    ip_url = "http://ip-api.com/json/?fields=lat,lon,city,regionName,country,status,message"
    with urllib.request.urlopen(ip_url, timeout=10) as resp:
        ip_data = json.loads(resp.read())
    if ip_data.get("status") != "success":
        raise RuntimeError(
            f"IP geolocation failed: {ip_data.get('message', 'unknown error')}"
        )
    city    = ip_data.get("city", "")
    region  = ip_data.get("regionName", "")
    country = ip_data.get("country", "")
    label   = ", ".join(p for p in [city, region, country] if p) or "Unknown location"
    return float(ip_data["lat"]), float(ip_data["lon"]), label


def fetch_weather_windows(lat: float, lon: float) -> list[dict]:
    """
    Fetch today's hourly weather from Open-Meteo (free, no API key).
    Returns a list of 24 dicts, one per hour, with running-relevant fields.
    """
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,apparent_temperature,precipitation_probability,"
        "windspeed_10m,weathercode,uv_index"
        "&timezone=auto&forecast_days=1"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())

    hourly = data["hourly"]
    hours = []
    for i, time_str in enumerate(hourly["time"]):
        hour = int(time_str[11:13])  # extract HH from "YYYY-MM-DDTHH:00"
        hours.append({
            "hour":       hour,
            "temp":       hourly["temperature_2m"][i],
            "feels_like": hourly["apparent_temperature"][i],
            "precip_prob": hourly["precipitation_probability"][i],
            "wind_kmh":   hourly["windspeed_10m"][i],
            "wmo_code":   hourly["weathercode"][i],
            "uv_index":   hourly["uv_index"][i],
        })
    return hours


def _wmo_description(code: int) -> str:
    """Short human-readable label for a WMO weather interpretation code."""
    if code == 0:        return "clear sky"
    elif code <= 2:      return "partly cloudy"
    elif code == 3:      return "overcast"
    elif code <= 48:     return "fog"
    elif code <= 55:     return "drizzle"
    elif code <= 65:     return "rain"
    elif code <= 67:     return "freezing rain"
    elif code <= 75:     return "snow"
    elif code <= 82:     return "rain showers"
    elif code <= 86:     return "snow showers"
    elif code <= 99:     return "thunderstorm"
    return "unknown"


def _score_hour(h: dict) -> tuple[int, list[str]]:
    """
    Score a single hour 0–100 for running suitability.
    Returns (score, list_of_condition_notes).
    Deductions are cumulative; score is clamped to 0.
    """
    score = 100
    notes: list[str] = []

    # ── Precipitation probability ──────────────────────────────────────────────
    precip = h["precip_prob"]
    if precip >= 70:
        score -= 40
        notes.append(f"{precip}% rain chance — likely wet")
    elif precip >= 40:
        score -= 20
        notes.append(f"{precip}% rain chance — possible showers")
    elif precip >= 20:
        score -= 5
        notes.append(f"{precip}% rain chance — slight risk")

    # ── WMO weather code ───────────────────────────────────────────────────────
    code = h["wmo_code"]
    if code >= 95:
        score -= 50
        notes.append("thunderstorm — do not run outdoors")
    elif code >= 71:
        score -= 30
        notes.append("snow — slippery underfoot")
    elif code >= 61:
        score -= 20
        notes.append("rain")
    elif code >= 51:
        score -= 10
        notes.append("drizzle")
    elif code >= 45:
        score -= 5
        notes.append("foggy")

    # ── Feels-like temperature ─────────────────────────────────────────────────
    feels = h["feels_like"]
    if feels > 28:
        score -= 25
        notes.append(f"feels like {feels:.0f}°C — very hot, heat stress risk")
    elif feels > 22:
        score -= 10
        notes.append(f"feels like {feels:.0f}°C — warm, carry water")
    elif feels < 0:
        score -= 15
        notes.append(f"feels like {feels:.0f}°C — below freezing, ice risk")
    elif feels < 5:
        score -= 5
        notes.append(f"feels like {feels:.0f}°C — cold, layer up")
    else:
        notes.append(f"feels like {feels:.0f}°C — comfortable")

    # ── Wind ───────────────────────────────────────────────────────────────────
    wind = h["wind_kmh"]
    if wind > 30:
        score -= 20
        notes.append(f"{wind:.0f} km/h wind — strong headwind likely")
    elif wind > 20:
        score -= 10
        notes.append(f"{wind:.0f} km/h wind — noticeable")
    elif wind > 12:
        score -= 2
        notes.append(f"{wind:.0f} km/h wind — light breeze")

    # ── UV index (only meaningful when running in exposed daylight) ────────────
    uv = h["uv_index"]
    if uv >= 8:
        score -= 10
        notes.append(f"UV index {uv:.0f} — very high, use sunscreen")
    elif uv >= 6:
        score -= 5
        notes.append(f"UV index {uv:.0f} — high")

    return max(0, score), notes


def find_best_run_window(hours: list[dict], is_weekday: bool) -> dict:
    """
    Evaluate candidate run windows for the day and rank them by score.

    Weekday: Morning (6–9am) and Lunch (12–1pm) only.
    Weekend: five windows spread across the day.
    Returns a dict with 'windows' (sorted best→worst) and 'recommended'.
    """
    if is_weekday:
        candidate_windows = {
            "Morning (6–9am)":  [h for h in hours if 6 <= h["hour"] <= 8],
            "Lunch (12–1pm)":   [h for h in hours if 12 <= h["hour"] <= 13],
        }
    else:
        candidate_windows = {
            "Morning (6–9am)":      [h for h in hours if 6  <= h["hour"] <= 8],
            "Mid-morning (9–11am)": [h for h in hours if 9  <= h["hour"] <= 10],
            "Lunch (12–2pm)":       [h for h in hours if 12 <= h["hour"] <= 13],
            "Afternoon (2–5pm)":    [h for h in hours if 14 <= h["hour"] <= 16],
            "Evening (5–8pm)":      [h for h in hours if 17 <= h["hour"] <= 19],
        }

    results = []
    for window_name, window_hours in candidate_windows.items():
        if not window_hours:
            continue
        # Pick the single best hour in the window
        best = max(window_hours, key=lambda h: _score_hour(h)[0])
        score, notes = _score_hour(best)
        results.append({
            "window":       window_name,
            "best_hour":    best["hour"],
            "score":        score,
            "temp":         best["temp"],
            "feels_like":   best["feels_like"],
            "precip_prob":  best["precip_prob"],
            "wind_kmh":     best["wind_kmh"],
            "wmo_code":     best["wmo_code"],
            "weather_desc": _wmo_description(best["wmo_code"]),
            "notes":        notes,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return {
        "is_weekday":  is_weekday,
        "windows":     results,
        "recommended": results[0] if results else None,
    }


# ─── Assessment helpers ───────────────────────────────────────────────────────

def assess_readiness(data):
    hrv_low, hrv_high, rhr_norm = load_baselines()

    hrv           = data["hrv"]
    rhr           = data["resting_hr"]
    sleep_hrs     = data["sleep_hours"]
    body_battery  = data["body_battery"]
    avg_stress    = data["avg_stress"]

    status = "green"
    flags  = []

    # HRV
    if hrv is None:
        flags.append("No HRV data — treat as amber")
        status = "amber"
    elif hrv < hrv_low - 10:
        flags.append(f"HRV {hrv} — well below baseline ({hrv_low}–{hrv_high})")
        status = "red"
    elif hrv < hrv_low:
        flags.append(f"HRV {hrv} — below baseline ({hrv_low}–{hrv_high})")
        if status == "green":
            status = "amber"
    else:
        flags.append(f"HRV {hrv} — normal (baseline {hrv_low}–{hrv_high})")

    # RHR
    if rhr and rhr > rhr_norm + 5:
        flags.append(f"Resting HR {rhr} bpm — elevated (+{rhr - rhr_norm} above norm of {rhr_norm})")
        if status == "green":
            status = "amber"
    elif rhr:
        flags.append(f"Resting HR {rhr} bpm — normal (norm {rhr_norm})")

    # Sleep
    if sleep_hrs and sleep_hrs < 5:
        flags.append(f"Sleep {sleep_hrs}h — insufficient")
        if status == "green":
            status = "amber"
    elif sleep_hrs and sleep_hrs < 6.5:
        flags.append(f"Sleep {sleep_hrs}h — suboptimal")
    elif sleep_hrs:
        flags.append(f"Sleep {sleep_hrs}h — adequate")

    # Body Battery
    if body_battery is not None:
        if body_battery < 25:
            flags.append(f"Body Battery {body_battery}/100 — depleted")
            status = "red"
        elif body_battery < 50:
            flags.append(f"Body Battery {body_battery}/100 — low")
            if status == "green":
                status = "amber"
        else:
            flags.append(f"Body Battery {body_battery}/100 — good")

    # Stress
    if avg_stress is not None:
        if avg_stress > 70:
            flags.append(f"Avg stress {avg_stress}/100 — high")
            if status == "green":
                status = "amber"
        elif avg_stress > 50:
            flags.append(f"Avg stress {avg_stress}/100 — moderate")
        else:
            flags.append(f"Avg stress {avg_stress}/100 — low")

    decisions = {
        "red":   "REST DAY. No running. Walk only if you need to move.",
        "amber": "EASY RUN ONLY. 25–30 min, HR under 155 bpm. No threshold, no intervals.",
        "green": "TRAIN AS PLANNED. You're recovered — execute your next scheduled session.",
    }
    return status, flags, decisions[status]


def assess_acwr(acwr):
    if acwr is None:
        return "Cannot compute — not enough training data."
    if acwr < 0.8:
        return f"ACWR {acwr} — underloading. Room to add volume carefully."
    elif acwr <= 1.3:
        return f"ACWR {acwr} — optimal range (0.8–1.3). Stay the course."
    elif acwr <= 1.5:
        return f"ACWR {acwr} — elevated. Back off volume this week."
    else:
        return f"ACWR {acwr} — danger zone (>1.5). Mandatory easy week. Injury risk is high."


# ─── Workout builders ────────────────────────────────────────────────────────

def build_running_steps(steps: list[dict]) -> list:
    """
    Recursively convert a list of step dicts into garminconnect step objects.

    Supported step types:
      warmup    – {"type": "warmup",    "duration_minutes": 10}
      easy      – {"type": "easy",      "duration_minutes": 20}
      interval  – {"type": "interval",  "duration_minutes": 2}   (time-based)
                  {"type": "interval",  "distance_meters":  400} (distance-based)
      recovery  – {"type": "recovery",  "duration_minutes": 1.5}
      cooldown  – {"type": "cooldown",  "duration_minutes": 10}
      repeat    – {"type": "repeat",    "iterations": 5, "steps": [...]}
    """
    result = []
    for i, step in enumerate(steps, start=1):
        step_type = step.get("type", "").lower()

        if step_type == "repeat":
            nested = build_running_steps(step["steps"])
            result.append(create_repeat_group(
                iterations=int(step["iterations"]),
                workout_steps=nested,
                step_order=i,
            ))

        elif step_type == "warmup":
            secs = float(step["duration_minutes"]) * 60
            result.append(create_warmup_step(duration_seconds=secs, step_order=i))

        elif step_type == "cooldown":
            secs = float(step["duration_minutes"]) * 60
            result.append(create_cooldown_step(duration_seconds=secs, step_order=i))

        elif step_type == "recovery":
            secs = float(step["duration_minutes"]) * 60
            result.append(create_recovery_step(duration_seconds=secs, step_order=i))

        elif step_type in ("interval", "easy"):
            if "distance_meters" in step:
                # NOTE: use COND_DISTANCE (3), NOT ConditionType.DISTANCE — the
                # library enum says 1, but Garmin id 1 is lap.button, which makes
                # the lap show up as "press lap button" instead of a distance lap.
                end_condition = {
                    "conditionTypeId": COND_DISTANCE,
                    "conditionTypeKey": "distance",
                    "displayOrder": 1,
                    "displayable": True,
                }
                end_value = float(step["distance_meters"])
            else:
                end_condition = {
                    "conditionTypeId": COND_TIME,
                    "conditionTypeKey": "time",
                    "displayOrder": 2,
                    "displayable": True,
                }
                end_value = float(step["duration_minutes"]) * 60

            result.append(ExecutableStep(
                stepOrder=i,
                stepType={
                    "stepTypeId": StepType.INTERVAL,
                    "stepTypeKey": "interval",
                    "displayOrder": 3,
                },
                endCondition=end_condition,
                endConditionValue=end_value,
                targetType={
                    "workoutTargetTypeId": TargetType.NO_TARGET,
                    "workoutTargetTypeKey": "no.target",
                    "displayOrder": 1,
                },
            ))

        else:
            raise ValueError(f"Unknown running step type: '{step_type}'")

    return result


# ─── Garmin exercise category / name mapping ────────────────────────────────
# Keys are lowercased search terms.  Values are (category, exerciseName) pairs
# that Garmin Connect recognises for structured strength-workout steps.
# Expand this dict as needed — unmatched exercises fall back to UNKNOWN.

GARMIN_EXERCISE_MAP: dict[str, tuple[str, str]] = {
    # Bench press variants
    "bench press":              ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "barbell bench press":      ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "barbell bench":            ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "flat bench press":         ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "dumbbell bench press":     ("BENCH_PRESS", "DUMBBELL_BENCH_PRESS"),
    "incline bench press":      ("BENCH_PRESS", "INCLINE_BARBELL_BENCH_PRESS"),
    "incline dumbbell press":   ("BENCH_PRESS", "INCLINE_DUMBBELL_BENCH_PRESS"),
    "decline bench press":      ("BENCH_PRESS", "DECLINE_BARBELL_BENCH_PRESS"),
    "close grip bench press":   ("BENCH_PRESS", "CLOSE_GRIP_BARBELL_BENCH_PRESS"),
    # Squat variants
    "squat":                    ("SQUAT", "BARBELL_BACK_SQUAT"),
    "back squat":               ("SQUAT", "BARBELL_BACK_SQUAT"),
    "barbell back squat":       ("SQUAT", "BARBELL_BACK_SQUAT"),
    "barbell squat":            ("SQUAT", "BARBELL_BACK_SQUAT"),
    "front squat":              ("SQUAT", "BARBELL_FRONT_SQUAT"),
    "goblet squat":             ("SQUAT", "GOBLET_SQUAT"),
    "dumbbell squat":           ("SQUAT", "DUMBBELL_SQUAT"),
    "bodyweight squat":         ("SQUAT", "BODYWEIGHT_SQUAT"),
    # Deadlift variants
    "deadlift":                 ("DEADLIFT", "BARBELL_DEADLIFT"),
    "barbell deadlift":         ("DEADLIFT", "BARBELL_DEADLIFT"),
    "conventional deadlift":    ("DEADLIFT", "BARBELL_DEADLIFT"),
    "romanian deadlift":        ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "rdl":                      ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "sumo deadlift":            ("DEADLIFT", "SUMO_DEADLIFT"),
    "stiff leg deadlift":       ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    # Overhead press / shoulder press
    "overhead press":           ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "ohp":                      ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "shoulder press":           ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "barbell overhead press":   ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "military press":           ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "dumbbell shoulder press":  ("SHOULDER_PRESS", "DUMBBELL_SHOULDER_PRESS"),
    "arnold press":             ("SHOULDER_PRESS", "ARNOLD_PRESS"),
    # Row variants
    "row":                      ("ROW", "BENT_OVER_ROW"),
    "barbell row":              ("ROW", "BENT_OVER_ROW"),
    "bent over row":            ("ROW", "BENT_OVER_ROW"),
    "pendlay row":              ("ROW", "BENT_OVER_ROW"),
    "dumbbell row":             ("ROW", "ONE_ARM_DUMBBELL_ROW"),
    "cable row":                ("ROW", "SEATED_CABLE_ROW"),
    "seated cable row":         ("ROW", "SEATED_CABLE_ROW"),
    "t-bar row":                ("ROW", "T_BAR_ROW"),
    # Curl variants
    "bicep curl":               ("CURL", "BARBELL_BICEPS_CURL"),
    "barbell curl":             ("CURL", "BARBELL_BICEPS_CURL"),
    "dumbbell curl":            ("CURL", "DUMBBELL_BICEPS_CURL"),
    "hammer curl":              ("CURL", "DUMBBELL_HAMMER_CURL"),
    "preacher curl":            ("CURL", "PREACHER_CURL"),
    # Triceps
    "tricep extension":         ("TRICEPS_EXTENSION", "OVERHEAD_TRICEPS_EXTENSION"),
    "skull crusher":            ("TRICEPS_EXTENSION", "LYING_TRICEPS_EXTENSION"),
    "tricep pushdown":          ("TRICEPS_EXTENSION", "TRICEPS_PRESSDOWN"),
    # Lateral raise
    "lateral raise":            ("LATERAL_RAISE", "LATERAL_RAISE_WITH_DUMBBELLS"),
    "dumbbell lateral raise":   ("LATERAL_RAISE", "LATERAL_RAISE_WITH_DUMBBELLS"),
    "front raise":              ("LATERAL_RAISE", "FRONT_RAISE"),
    # Lunge
    "lunge":                    ("LUNGE", "BARBELL_LUNGE"),
    "barbell lunge":            ("LUNGE", "BARBELL_LUNGE"),
    "dumbbell lunge":           ("LUNGE", "DUMBBELL_LUNGE"),
    "walking lunge":            ("LUNGE", "WALKING_LUNGE"),
    # Pull-up / chin-up
    "pull up":                  ("PULL_UP", "PULL_UP"),
    "pullup":                   ("PULL_UP", "PULL_UP"),
    "chin up":                  ("PULL_UP", "CHIN_UP"),
    "chinup":                   ("PULL_UP", "CHIN_UP"),
    "lat pulldown":             ("PULL_UP", "LAT_PULLDOWN"),
    # Push-up
    "push up":                  ("PUSH_UP", "PUSH_UP"),
    "pushup":                   ("PUSH_UP", "PUSH_UP"),
    # Leg curl / extension
    "leg curl":                 ("LEG_CURL", "LEG_CURL"),
    "leg extension":            ("LEG_CURL", "LEG_EXTENSION"),
    # Calf raise
    "calf raise":               ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "standing calf raise":      ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "seated calf raise":        ("CALF_RAISE", "SEATED_CALF_RAISE"),
    # Hip raise
    "hip thrust":               ("HIP_RAISE", "BARBELL_HIP_THRUST"),
    "glute bridge":             ("HIP_RAISE", "WEIGHTED_GLUTE_BRIDGE"),
    # Shrug
    "shrug":                    ("SHRUG", "BARBELL_SHRUG"),
    "barbell shrug":            ("SHRUG", "BARBELL_SHRUG"),
    "dumbbell shrug":           ("SHRUG", "DUMBBELL_SHRUG"),
    # Flye
    "fly":                      ("FLYE", "DUMBBELL_FLYE"),
    "flye":                     ("FLYE", "DUMBBELL_FLYE"),
    "dumbbell fly":             ("FLYE", "DUMBBELL_FLYE"),
    "cable fly":                ("FLYE", "CABLE_CROSSOVER"),
    "cable crossover":          ("FLYE", "CABLE_CROSSOVER"),
    # Plank
    "plank":                    ("PLANK", "PLANK"),
    # Core / crunch
    "crunch":                   ("CRUNCH", "CRUNCH"),
    "sit up":                   ("SIT_UP", "SIT_UP"),
    "situp":                    ("SIT_UP", "SIT_UP"),
    "leg raise":                ("LEG_RAISE", "HANGING_LEG_RAISE"),
    "hanging leg raise":        ("LEG_RAISE", "HANGING_LEG_RAISE"),
}


def _lookup_garmin_exercise(name: str) -> tuple[str, str]:
    """
    Look up (category, exerciseName) for a given free-text exercise name.
    Returns ("UNKNOWN", "UNKNOWN") if no match is found.
    """
    key = name.strip().lower()

    # Exact match
    if key in GARMIN_EXERCISE_MAP:
        return GARMIN_EXERCISE_MAP[key]

    # Substring match — pick the longest matching key
    best_match = None
    best_len = 0
    for map_key in GARMIN_EXERCISE_MAP:
        if map_key in key and len(map_key) > best_len:
            best_match = map_key
            best_len = len(map_key)
    if best_match:
        return GARMIN_EXERCISE_MAP[best_match]

    return ("UNKNOWN", "UNKNOWN")


def build_strength_steps(exercises: list[dict]) -> list:
    """
    Convert a list of exercise dicts into structured Garmin workout steps.

    Each exercise becomes a RepeatGroup (iterations = sets) containing:
      1. An exercise step with rep-based end condition + category/exerciseName
      2. A rest step between sets

    Format: {"name": "Squat", "sets": 4, "reps": 8, "rest_seconds": 90}
    """
    steps = []
    order = 1
    for ex in exercises:
        name       = ex["name"]
        sets       = int(ex.get("sets", 3))
        reps       = int(ex.get("reps", 10))
        rest_secs  = float(ex.get("rest_seconds", 60))

        category, exercise_name = _lookup_garmin_exercise(name)

        # Build the inner steps for one set: exercise + rest
        exercise_step = ExecutableStep(
            stepOrder=1,
            stepType={
                "stepTypeId": StepType.INTERVAL,
                "stepTypeKey": "interval",
                "displayOrder": 3,
            },
            endCondition={
                "conditionTypeId": 10,
                "conditionTypeKey": "reps",
                "displayOrder": 10,
                "displayable": True,
            },
            endConditionValue=float(reps),
            targetType={
                "workoutTargetTypeId": TargetType.NO_TARGET,
                "workoutTargetTypeKey": "no.target",
                "displayOrder": 1,
            },
            category=category,
            exerciseName=exercise_name,
            description=f"{name} ({reps} reps)",
        )

        rest_step = ExecutableStep(
            stepOrder=2,
            stepType={
                "stepTypeId": StepType.REST,
                "stepTypeKey": "rest",
                "displayOrder": 5,
            },
            endCondition={
                "conditionTypeId": 8,
                "conditionTypeKey": "fixed.rest",
                "displayOrder": 8,
                "displayable": True,
            },
            endConditionValue=rest_secs,
            targetType={
                "workoutTargetTypeId": TargetType.NO_TARGET,
                "workoutTargetTypeKey": "no.target",
                "displayOrder": 1,
            },
        )

        # Wrap in a RepeatGroup for N sets
        group = RepeatGroup(
            stepOrder=order,
            stepType={
                "stepTypeId": StepType.REPEAT,
                "stepTypeKey": "repeat",
                "displayOrder": 6,
            },
            numberOfIterations=sets,
            workoutSteps=[exercise_step, rest_step],
            endCondition={
                "conditionTypeId": ConditionType.ITERATIONS,
                "conditionTypeKey": "iterations",
                "displayOrder": 7,
                "displayable": False,
            },
            endConditionValue=float(sets),
        )

        steps.append(group)
        order += 1

    return steps


# ─── Race predictions ────────────────────────────────────────────────────────

def fetch_garmin_race_predictions() -> dict:
    """
    Fetch Garmin's built-in race predictions (derived from VO2 Max + training
    patterns).  Returns a dict with keys 'hm', '10k', '5k' as time strings
    ('H:MM:SS' / 'M:SS'), plus 'vo2max' and 'source'.

    Falls back to VO2 Max table estimation if race predictions are unavailable.
    All values may be None if Garmin has insufficient data.
    """
    client = get_client()
    today  = date.today().isoformat()
    result = {"hm": None, "10k": None, "5k": None, "vo2max": None, "source": None}

    # ── Try race predictions endpoint ─────────────────────────────────────────
    try:
        preds = client.get_race_predictions()
        if preds and isinstance(preds, dict):
            def _fmt(secs):
                if secs is None:
                    return None
                s = int(secs)
                h, rem = divmod(s, 3600)
                m, sec = divmod(rem, 60)
                return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

            result["hm"]  = _fmt(preds.get("timeInSecHalfMarathon") or preds.get("halfMarathon"))
            result["10k"] = _fmt(preds.get("timeInSec10K") or preds.get("tenK"))
            result["5k"]  = _fmt(preds.get("timeInSec5K") or preds.get("fiveK"))
            if any(v is not None for v in [result["hm"], result["10k"], result["5k"]]):
                result["source"] = "garmin_race_predictor"
    except Exception:
        pass

    # ── Pull VO2 Max regardless (used as fallback and stored in plan) ─────────
    try:
        metrics = client.get_max_metrics(today)
        if metrics and isinstance(metrics, list):
            for m in metrics:
                vo2 = (m.get("generic") or {}).get("vo2MaxPreciseValue")
                if vo2:
                    result["vo2max"] = round(float(vo2), 1)
                    break
    except Exception:
        pass

    # ── VO2 Max table fallback if race predictor gave nothing ─────────────────
    if result["hm"] is None and result["vo2max"] is not None:
        hm_time, _ = vo2max_to_hm_prediction(result["vo2max"])
        result["hm"]    = hm_time
        result["source"] = "vo2max_table_estimate"

    if result["source"] is None:
        result["source"] = "no_data"

    return result


# ─── MCP tool registry ────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
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
                            "Ordered list of steps. Each step is an object with 'type' plus duration/distance. "
                            "Types: warmup, easy, interval, recovery, cooldown, repeat. "
                            "Use duration_minutes for time-based, distance_meters for distance-based. "
                            "Repeat steps require 'iterations' and a nested 'steps' list. "
                            "Example: [{\"type\":\"warmup\",\"duration_minutes\":10},"
                            "{\"type\":\"repeat\",\"iterations\":5,\"steps\":["
                            "{\"type\":\"interval\",\"distance_meters\":400},"
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
                "Each exercise becomes a set of timed work+rest steps on the watch. "
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
                            "reps (int), rest_seconds (int). "
                            "Example: [{\"name\":\"Squat\",\"sets\":4,\"reps\":6,\"rest_seconds\":120}]"
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
    ]


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
        data = fetch_morning_data()
        status, flags, decision = assess_readiness(data)

        bb_str     = f"{data['body_battery']}/100" if data['body_battery'] is not None else "N/A"
        stress_str = f"{data['avg_stress']}/100"   if data['avg_stress']   is not None else "N/A"

        summary = (
            f"DATE: {date.today().strftime('%A %d %B %Y')}\n\n"
            f"READINESS METRICS:\n"
            f"  HRV:          {data['hrv'] or 'N/A'}\n"
            f"  Resting HR:   {data['resting_hr'] or 'N/A'} bpm\n"
            f"  Sleep:        {data['sleep_hours'] or 'N/A'}h\n"
            f"  Body Battery: {bb_str}\n"
            f"  Avg Stress:   {stress_str}\n\n"
            f"FLAGS:\n" + "\n".join(f"  - {f}" for f in flags) + "\n\n"
            f"STATUS: {status.upper()}\n\n"
            f"TODAY: {decision}"
        )
        plan = load_plan()
        summary += format_plan_context(plan) if plan else _NO_PLAN_NUDGE
        return [types.TextContent(type="text", text=summary)]

    elif name == "get_training_load":
        data = fetch_training_load()
        acwr_assessment = assess_acwr(data["acwr"])

        summary = (
            f"TRAINING LOAD — {date.today().strftime('%A %d %B %Y')}\n\n"
            f"  Training Status:        {data['training_status'] or 'N/A'}\n"
            f"  Garmin Load (7d):       {data['training_load_7d'] or 'N/A'}\n"
            f"  Acute Load (7d):        {data['acute_load']}\n"
            f"  Chronic Load (4wk avg): {data['chronic_load']}\n"
            f"  ACWR:                   {data['acwr'] or 'N/A'}\n"
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

        # ── Header ────────────────────────────────────────────────────────
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

        # ── Running dynamics ──────────────────────────────────────────────
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

        # ── Splits ────────────────────────────────────────────────────────
        if data["splits"]:
            lines.append("\nSPLITS:")
            lines.append(f"  {'#':<4} {'Dist':>6} {'Pace':>10} {'HR':>6} {'Cadence':>8} {'Elev+':>7}")
            lines.append(f"  {'—'*4} {'—'*6} {'—'*10} {'—'*6} {'—'*8} {'—'*7}")
            for s in data["splits"]:
                hr_str = str(s['avg_hr']) if s['avg_hr'] else "—"
                cad_str = f"{s['avg_cadence']:.0f}" if s['avg_cadence'] else "—"
                elev_str = f"{s['elevation_gain']:.0f}m" if s['elevation_gain'] is not None else "—"
                lines.append(
                    f"  {s['split']:<4} {s['distance_km']:>5.2f}km {s['pace']:>10} "
                    f"{hr_str:>6} {cad_str:>8} {elev_str:>7}"
                )

        # ── HR zones ──────────────────────────────────────────────────────
        if data["hr_zones"]:
            lines.append("\nHR ZONE BREAKDOWN:")
            for z in data["hr_zones"]:
                bar_len = round(z["pct_of_total"] / 2)  # scale to ~50 char max
                bar = "█" * bar_len
                lines.append(
                    f"  Zone {z['zone'] or '?'} ({z['range']}): "
                    f"{z['time_min']:.1f} min ({z['pct_of_total']:.1f}%)  {bar}"
                )

        # ── Form recommendations ──────────────────────────────────────────
        recs = analyze_running_form(data)
        lines.append("\nFORM ANALYSIS:")
        for rec in recs:
            lines.append(f"  • {rec}")

        plan = load_plan()
        lines.append(format_plan_context(plan) if plan else _NO_PLAN_NUDGE)
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "create_running_workout":
        workout_name = arguments["workout_name"]
        description  = arguments.get("description")
        steps_raw    = arguments["steps"]

        built_steps = build_running_steps(steps_raw)
        total_secs  = sum(
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

        client = get_client()
        result = client.upload_running_workout(workout)
        workout_id = result.get("workoutId") or result.get("workout_id") or result.get("id")

        summary = (
            f"RUNNING WORKOUT CREATED\n"
            f"  Name:       {workout_name}\n"
            f"  Workout ID: {workout_id}\n"
            f"  Steps:      {len(built_steps)}\n"
            f"\nUse schedule_workout with workout_id={workout_id} to add it to your calendar."
        )
        return [types.TextContent(type="text", text=summary)]

    elif name == "create_strength_workout":
        workout_name = arguments["workout_name"]
        description  = arguments.get("description")
        exercises    = arguments["exercises"]

        built_steps = build_strength_steps(exercises)

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

        # Use strength_training sport type (sportTypeId 5)
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

        client = get_client()
        result = client.upload_workout(workout.to_dict())
        workout_id = result.get("workoutId") or result.get("workout_id") or result.get("id")

        ex_summary = ", ".join(
            f"{ex['name']} {ex.get('sets',3)}×{ex.get('reps',10)}"
            for ex in exercises
        )
        summary = (
            f"STRENGTH WORKOUT CREATED\n"
            f"  Name:       {workout_name}\n"
            f"  Workout ID: {workout_id}\n"
            f"  Exercises:  {ex_summary}\n"
            f"\nUse schedule_workout with workout_id={workout_id} to add it to your calendar."
        )
        return [types.TextContent(type="text", text=summary)]

    elif name == "schedule_workout":
        workout_id = int(arguments["workout_id"])
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

    elif name == "get_weekly_review":
        this_week = fetch_weekly_summary(0)
        last_week = fetch_weekly_summary(1)
        load_data = fetch_training_load()

        # Volume delta % (distance)
        vol_delta_pct = None
        if last_week["total_distance_km"] > 0:
            vol_delta_pct = round(
                (this_week["total_distance_km"] - last_week["total_distance_km"])
                / last_week["total_distance_km"] * 100, 1
            )

        recommendation = _week_recommendation(load_data["acwr"], vol_delta_pct)

        def fmt_types(tc: dict) -> str:
            return ", ".join(f"{k} ×{v}" for k, v in sorted(tc.items())) or "none"

        def delta_str(a, b, unit="", decimals=1) -> str:
            if a is None or b is None:
                return "N/A"
            d = round(a - b, decimals)
            return f"{'+' if d >= 0 else ''}{d}{unit}"

        lines = [
            f"WEEKLY REVIEW — w/c {this_week['start']}",
            "",
            f"{'Metric':<28} {'This week':>12} {'Last week':>12} {'Δ':>10}",
            f"{'-'*28} {'-'*12} {'-'*12} {'-'*10}",
            f"{'Sessions':<28} {this_week['session_count']:>12} {last_week['session_count']:>12} "
            f"{delta_str(this_week['session_count'], last_week['session_count'], '', 0):>10}",
            f"{'Total distance (km)':<28} {this_week['total_distance_km']:>12} {last_week['total_distance_km']:>12} "
            f"{delta_str(this_week['total_distance_km'], last_week['total_distance_km'], 'km'):>10}",
            f"{'Running distance (km)':<28} {this_week['run_distance_km']:>12} {last_week['run_distance_km']:>12} "
            f"{delta_str(this_week['run_distance_km'], last_week['run_distance_km'], 'km'):>10}",
            f"{'Longest run (km)':<28} {this_week['longest_run_km']:>12} {last_week['longest_run_km']:>12} "
            f"{delta_str(this_week['longest_run_km'], last_week['longest_run_km'], 'km'):>10}",
            f"{'Total time (min)':<28} {this_week['total_duration_min']:>12} {last_week['total_duration_min']:>12} "
            f"{delta_str(this_week['total_duration_min'], last_week['total_duration_min'], 'min'):>10}",
        ]

        if vol_delta_pct is not None:
            lines.append(f"\nVolume vs last week: {'+' if vol_delta_pct >= 0 else ''}{vol_delta_pct}%")

        lines.append(f"\nActivity mix this week:  {fmt_types(this_week['type_counts'])}")
        lines.append(f"Activity mix last week:  {fmt_types(last_week['type_counts'])}")

        if this_week["best_session"]:
            bs = this_week["best_session"]
            lines.append(
                f"\nBest session: {bs['date']}  {bs['name']} — "
                f"{bs['distance_km']}km in {bs['duration_min']}min  "
                f"(aerobic TE: {bs['aerobic_te']})"
            )

        lines.append(f"\nACWR: {load_data['acwr'] or 'N/A'}  |  "
                     f"Acute load: {load_data['acute_load']}  |  "
                     f"Chronic load: {load_data['chronic_load']}")
        lines.append(f"\nNEXT WEEK: {recommendation}")

        plan = load_plan()
        lines.append(format_plan_context(plan) if plan else _NO_PLAN_NUDGE)
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_monthly_review":
        this_month = fetch_monthly_summary(0)
        last_month = fetch_monthly_summary(1)
        trend      = fetch_fitness_trend()

        # VO2 Max: first and last non-None values in the 6-week trend
        vo2_values = [e["vo2max"] for e in trend if e["vo2max"] is not None]
        vo2_start  = vo2_values[0]  if vo2_values else None
        vo2_latest = vo2_values[-1] if vo2_values else None
        vo2_delta  = round(vo2_latest - vo2_start, 1) if (vo2_start and vo2_latest) else None

        def delta_str(a, b, unit="", decimals=1) -> str:
            if a is None or b is None:
                return "N/A"
            d = round(a - b, decimals)
            return f"{'+' if d >= 0 else ''}{d}{unit}"

        consistency_str = (
            f"{this_month['consistent_weeks']}/{this_month['total_weeks']} weeks with 3+ sessions"
            if this_month["total_weeks"] > 0
            else "N/A"
        )
        last_consistency_str = (
            f"{last_month['consistent_weeks']}/{last_month['total_weeks']} weeks with 3+ sessions"
            if last_month["total_weeks"] > 0
            else "N/A"
        )

        lines = [
            f"MONTHLY REVIEW — {this_month['month_label']} vs {last_month['month_label']}",
            "",
            f"{'Metric':<30} {this_month['month_label'][:12]:>14} {last_month['month_label'][:12]:>14} {'Δ':>10}",
            f"{'-'*30} {'-'*14} {'-'*14} {'-'*10}",
            f"{'Sessions':<30} {this_month['session_count']:>14} {last_month['session_count']:>14} "
            f"{delta_str(this_month['session_count'], last_month['session_count'], '', 0):>10}",
            f"{'Total distance (km)':<30} {this_month['total_distance_km']:>14} {last_month['total_distance_km']:>14} "
            f"{delta_str(this_month['total_distance_km'], last_month['total_distance_km'], 'km'):>10}",
            f"{'Running distance (km)':<30} {this_month['run_distance_km']:>14} {last_month['run_distance_km']:>14} "
            f"{delta_str(this_month['run_distance_km'], last_month['run_distance_km'], 'km'):>10}",
            f"{'Longest run (km)':<30} {this_month['longest_run_km']:>14} {last_month['longest_run_km']:>14} "
            f"{delta_str(this_month['longest_run_km'], last_month['longest_run_km'], 'km'):>10}",
            f"{'Total time (min)':<30} {this_month['total_duration_min']:>14} {last_month['total_duration_min']:>14} "
            f"{delta_str(this_month['total_duration_min'], last_month['total_duration_min'], 'min'):>10}",
        ]

        lines.append(f"\nConsistency this month:  {consistency_str}")
        lines.append(f"Consistency last month:  {last_consistency_str}")

        if vo2_latest is not None:
            vo2_line = f"\nVO2 Max (6-week window): {vo2_latest}"
            if vo2_delta is not None:
                direction = "↑" if vo2_delta > 0.5 else "↓" if vo2_delta < -0.5 else "→"
                vo2_line += f"  ({direction} {'+' if vo2_delta >= 0 else ''}{vo2_delta} over 6 weeks)"
            lines.append(vo2_line)

        # Focus area for next month
        dist_delta = (
            round(this_month["total_distance_km"] - last_month["total_distance_km"], 1)
            if last_month["total_distance_km"] > 0 else None
        )
        consistency_good = this_month["consistent_weeks"] >= max(this_month["total_weeks"] - 1, 1)

        if not consistency_good:
            focus = "Consistency first. Hit 3+ sessions every week before adding volume."
        elif dist_delta is not None and dist_delta < 0:
            focus = "Volume dropped this month. Rebuild gradually — aim for +10% on last month."
        elif vo2_delta is not None and vo2_delta < -0.5:
            focus = "VO2 Max declining. Introduce one quality session per week (tempo or intervals)."
        elif dist_delta is not None and dist_delta > last_month["total_distance_km"] * 0.15:
            focus = "Big volume jump this month. Consolidate — maintain distance, add one quality day."
        else:
            focus = "Solid month. Stay consistent and progress the long run by ~1km per week."

        lines.append(f"\nNEXT MONTH FOCUS: {focus}")

        plan = load_plan()
        lines.append(format_plan_context(plan) if plan else _NO_PLAN_NUDGE)
        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "get_run_window":
        location_arg = arguments.get("location") or None
        try:
            lat, lon, place_label = resolve_location(location_arg)
        except Exception as exc:
            return [types.TextContent(
                type="text",
                text=f"RUN WINDOW ERROR — could not determine location: {exc}"
            )]

        today = date.today()
        is_weekday = today.weekday() < 5  # Monday=0 … Sunday=6

        hours = fetch_weather_windows(lat, lon)
        result = find_best_run_window(hours, is_weekday)

        day_type = "Weekday" if is_weekday else "Weekend"
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

        # ── Step 1: no required args yet — fetch fitness data + return questionnaire ──
        if not race_date_str or not training_days:
            preds = fetch_garmin_race_predictions()

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
            ]

            return [types.TextContent(
                type="text",
                text="\n".join(fitness_lines) + "\n" + "\n".join(questions) +
                     "\n\nOnce you have the answers, call setup_training_plan again with the full arguments."
            )]

        # ── Step 2: required args present — build and save the plan ──────────────
        preds = fetch_garmin_race_predictions()

        race_date    = date.fromisoformat(race_date_str)
        today        = date.today()
        total_weeks  = max(1, (race_date - today).days // 7)
        race_dist    = arguments.get("race_distance", "half_marathon")
        inc_strength = arguments.get("include_strength", True)
        str_days     = arguments.get("strength_days_per_week", 2) if inc_strength else 0
        run_days     = max(1, int(training_days) - str_days)
        target_time  = arguments.get("target_time") or preds.get("hm")

        raw_blocked = arguments.get("blocked_days") or []
        # Normalise to title-case day names, strip 'none'
        day_names = {"monday","tuesday","wednesday","thursday","friday","saturday","sunday"}
        blocked_days = [
            d.strip().title() for d in raw_blocked
            if d.strip().lower() in day_names
        ]

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
            "methodology":            "polarised_80_20",
            "target_time":            target_time,
            "predicted_hm_time":      preds.get("hm"),
            "vo2max_at_setup":        preds.get("vo2max"),
            "prediction_source":      preds.get("source"),
        }
        save_plan(plan)

        # ── Format confirmation output ────────────────────────────────────────
        phase_breakdown = []
        phase_names = ["Base", "Development", "Specific", "Taper", "Race Week"]
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
            f"    Methodology:   Polarised 80/20 (easy aerobic base + 20% quality)",
            f"",
            f"  PHASE BREAKDOWN:",
        ]
        for ph, start_week, start_date in phase_breakdown:
            lines.append(f"    Week {start_week:>2}+  {ph:<12}  (from {start_date.strftime('%d %b')})")

        if plan["b_race_name"]:
            lines.append(f"\n  B Race: {plan['b_race_name']} — {plan['b_race_date']}")

        lines.append(f"\nPlan saved to {PLAN_FILE}")
        lines.append("All daily coaching tools will now include plan context automatically.")
        return [types.TextContent(type="text", text="\n".join(lines))]

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
        lines.append(f"    Methodology:   Polarised 80/20")

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
        return [types.TextContent(type="text", text="\n".join(lines))]

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
        print(assess_acwr(load["acwr"]))

        print("\n=== Fitness Trend ===")
        for entry in fetch_fitness_trend():
            print(f"  {entry['date']}  VO2: {entry['vo2max']}")

        print("\n=== Recent Activities ===")
        for act in fetch_recent_activities():
            print(f"  {act['date']}  {act['name']}  {act['distance_km']}km  {act['avg_pace']}")
    else:
        asyncio.run(main())
