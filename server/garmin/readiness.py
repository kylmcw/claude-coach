from datetime import date, timedelta

from garmin.calibration import load_baselines
from garmin.client import get_client
from coaching.thresholds import get_zones, easy_hr_ceiling

# Cache LT zones per day to avoid a live API call on every readiness check.
_cached_zones: dict | None = None
_cached_zones_date: str | None = None


def _get_cached_zones() -> dict:
    global _cached_zones, _cached_zones_date
    today = date.today().isoformat()
    if _cached_zones is not None and _cached_zones_date == today:
        return _cached_zones
    try:
        _cached_zones = get_zones()
        _cached_zones_date = today
    except Exception:
        _cached_zones = {}
        _cached_zones_date = today
    return _cached_zones


def fetch_morning_data() -> dict:
    """
    HRV, RHR, sleep (with stages + score), body battery, stress, Training Readiness.
    Includes staleness flags for each metric so callers can warn on unsynced watches.
    """
    client    = get_client()
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Each call is isolated so one API failure doesn't wipe out all metrics.
    try:
        hr_data = client.get_heart_rates(today)
    except Exception:
        hr_data = {}
    try:
        hrv_data = client.get_hrv_data(today)
    except Exception:
        hrv_data = None
    try:
        sleep_data = client.get_sleep_data(today)
    except Exception:
        sleep_data = None
    try:
        bb_data = client.get_body_battery(yesterday, today)
    except Exception:
        bb_data = []
    try:
        stress_data = client.get_stress_data(yesterday)
    except Exception:
        stress_data = None

    # Training Readiness (Garmin native 0–100 score)
    tr_score   = None
    tr_level   = None
    tr_factors = {}
    try:
        tr_raw = client.get_morning_training_readiness(today)
        if tr_raw and isinstance(tr_raw, dict):
            tr_score = tr_raw.get("score") or tr_raw.get("trainingReadinessScore")
            tr_level = tr_raw.get("level") or tr_raw.get("trainingReadinessLevel")
            # Contributing factors vary by firmware — capture what's present
            for key in ("sleepScore", "recoveryTime", "acuteLoad", "stressHistory",
                        "hrvStatus", "acuteLoadStatus", "sleepScoreStatus", "overall"):
                v = tr_raw.get(key)
                if v is not None:
                    tr_factors[key] = v
    except Exception:
        pass

    # RHR
    resting_hr = hr_data.get("restingHeartRate")
    hr_date    = (hr_data.get("dateTime") or today)[:10]

    # HRV — last-night average + status from Garmin's own 5-week baseline.
    # Key name varies by API version: "hrvSummary" (camelCase) or "hrv_summary" (snake_case).
    hrv_nightly    = None
    hrv_status     = None
    hrv_weekly_avg = None
    hrv_date       = None
    if hrv_data and isinstance(hrv_data, dict):
        s = hrv_data.get("hrvSummary") or hrv_data.get("hrv_summary") or {}
        hrv_nightly    = s.get("lastNightAvg") or s.get("lastNightAverage")
        hrv_status     = s.get("status") or s.get("hrvStatus")
        hrv_weekly_avg = s.get("weeklyAvg") or s.get("weeklyAverage")
        hrv_date       = s.get("calendarDate") or hrv_data.get("calendarDate")

    # Sleep — full stage breakdown
    sleep_hours     = None
    sleep_score     = None
    deep_hours      = None
    rem_hours       = None
    light_hours     = None
    awake_hours     = None
    sleep_date      = None
    if sleep_data and "dailySleepDTO" in sleep_data:
        dto   = sleep_data["dailySleepDTO"]
        sleep_date = dto.get("calendarDate")
        start = dto.get("sleepStartTimestampGMT")
        end   = dto.get("sleepEndTimestampGMT")
        if start and end:
            sleep_hours = round((end - start) / 3_600_000, 1)
        else:
            secs = (dto.get("sleepTimeSeconds") or 0) + (dto.get("awakeSleepSeconds") or 0)
            if secs:
                sleep_hours = round(secs / 3600, 1)

        # Sleep stages
        def _ms_to_h(ms):
            return round(ms / 3_600_000, 1) if ms else None

        def _sec_to_h(s):
            return round(s / 3600, 1) if s else None

        deep_hours  = (_ms_to_h(dto.get("deepSleepMilliseconds"))
                       or _sec_to_h(dto.get("deepSleepSeconds")))
        rem_hours   = (_ms_to_h(dto.get("remSleepMilliseconds"))
                       or _sec_to_h(dto.get("remSleepSeconds")))
        light_hours = (_ms_to_h(dto.get("lightSleepMilliseconds"))
                       or _sec_to_h(dto.get("lightSleepSeconds")))
        awake_hours = _sec_to_h(dto.get("awakeSleepSeconds"))

        # Sleep score — sleepScores is inside dailySleepDTO, not at top level
        scores = dto.get("sleepScores") or sleep_data.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        sleep_score = (
            overall.get("value") if isinstance(overall, dict) else overall
        ) or scores.get("totalScore")

    # Body Battery — peak charged value from yesterday/today window
    body_battery      = None
    body_battery_date = None
    if bb_data:
        for entry in bb_data:
            if isinstance(entry, dict) and "bodyBatteryValuesArray" in entry:
                vals = [v[1] for v in entry["bodyBatteryValuesArray"] if v[1] is not None]
                if vals:
                    body_battery = max(vals)
                    body_battery_date = entry.get("date")

    # Stress — daily average (ignoring -1 rest/unmeasured periods)
    avg_stress      = None
    stress_date_key = None
    if stress_data and "allStressData" in stress_data:
        asd = stress_data["allStressData"]
        stress_date_key = asd.get("calendarDate")
        raw = asd.get("stressValuesArray", [])
        levels = [s["stressLevel"] for s in raw if s.get("stressLevel", -1) > 0]
        if levels:
            avg_stress = round(sum(levels) / len(levels))

    # ── Stale-data detection ──────────────────────────────────────────────
    # Warn when a key metric's date doesn't match today (watch not synced).
    stale_warnings = []
    if hrv_nightly is None and sleep_hours is None and body_battery is None:
        stale_warnings.append(
            "HRV, Sleep, and Body Battery unavailable — watch hasn't synced yet. "
            "Open Garmin Connect on your phone, wait for sync, then retry."
        )
    if hrv_date and hrv_date != today:
        stale_warnings.append(f"HRV data is from {hrv_date} — sync your watch for today's reading")
    if sleep_date and sleep_date != today:
        stale_warnings.append(f"Sleep data is from {sleep_date} — sync your watch")
    if hr_date != today:
        stale_warnings.append(f"Heart rate data is from {hr_date} — sync your watch")

    return {
        "hrv":              hrv_nightly,
        "hrv_status":       hrv_status,
        "hrv_weekly_avg":   hrv_weekly_avg,
        "resting_hr":       resting_hr,
        "sleep_hours":      sleep_hours,
        "sleep_score":      sleep_score,
        "deep_hours":       deep_hours,
        "rem_hours":        rem_hours,
        "light_hours":      light_hours,
        "awake_hours":      awake_hours,
        "body_battery":     body_battery,
        "avg_stress":       avg_stress,
        "tr_score":         tr_score,
        "tr_level":         tr_level,
        "tr_factors":       tr_factors,
        "stale_warnings":   stale_warnings,
    }


def assess_readiness(data: dict) -> tuple[str, list[str], str]:
    hrv_low, hrv_high, rhr_norm = load_baselines()

    hrv          = data["hrv"]
    hrv_status   = data.get("hrv_status")
    rhr          = data["resting_hr"]
    sleep_hrs    = data["sleep_hours"]
    sleep_score  = data.get("sleep_score")
    body_battery = data["body_battery"]
    avg_stress   = data["avg_stress"]
    tr_score     = data.get("tr_score")

    # Personalized easy HR ceiling from LT (replaces hardcoded 155bpm).
    # Uses module-level cache — no live API call if zones were already fetched today.
    try:
        zones = _get_cached_zones()
        easy_hr_cap = easy_hr_ceiling(zones)
    except Exception:
        easy_hr_cap = 152

    status = "green"
    flags  = []

    # ── Training Readiness (Garmin native score) ──────────────────────────
    if tr_score is not None:
        tr_lvl = data.get("tr_level") or ""
        if tr_score >= 75:
            flags.append(f"Training Readiness: {tr_score}/100 — {tr_lvl or 'high'} (ready to train hard)")
        elif tr_score >= 50:
            flags.append(f"Training Readiness: {tr_score}/100 — {tr_lvl or 'moderate'}")
        elif tr_score >= 25:
            flags.append(f"Training Readiness: {tr_score}/100 — {tr_lvl or 'low'}")
            if status == "green":
                status = "amber"
        else:
            flags.append(f"Training Readiness: {tr_score}/100 — very low")
            status = "red"

    # ── HRV ──────────────────────────────────────────────────────────────
    if hrv is None:
        flags.append("No HRV data — treat as amber")
        if status == "green":
            status = "amber"
    else:
        hrv_note = f"(Garmin status: {hrv_status})" if hrv_status else ""
        if hrv < hrv_low - 10:
            flags.append(f"HRV {hrv} — well below baseline ({hrv_low}–{hrv_high}) {hrv_note}".strip())
            status = "red"
        elif hrv < hrv_low:
            flags.append(f"HRV {hrv} — below baseline ({hrv_low}–{hrv_high}) {hrv_note}".strip())
            if status == "green":
                status = "amber"
        else:
            flags.append(f"HRV {hrv} — normal (baseline {hrv_low}–{hrv_high}) {hrv_note}".strip())

    # Garmin HRV status override (uses Garmin's own 5-week baseline)
    if hrv_status == "LOW":
        if status == "green":
            status = "amber"
        flags.append("Garmin HRV status: LOW — nervous system under stress")
    elif hrv_status == "UNBALANCED":
        if status == "green":
            status = "amber"
        flags.append("Garmin HRV status: UNBALANCED — recovery trending poor")

    # ── RHR ──────────────────────────────────────────────────────────────
    if rhr and rhr > rhr_norm + 5:
        flags.append(f"Resting HR {rhr} bpm — elevated (+{rhr - rhr_norm} above norm of {rhr_norm})")
        if status == "green":
            status = "amber"
    elif rhr:
        flags.append(f"Resting HR {rhr} bpm — normal (norm {rhr_norm})")

    # ── Sleep score (primary) + hours (secondary) ─────────────────────────
    if sleep_score is not None:
        if sleep_score < 60:
            flags.append(f"Sleep Score {sleep_score}/100 — poor")
            if status == "green":
                status = "amber"
        elif sleep_score < 75:
            flags.append(f"Sleep Score {sleep_score}/100 — fair")
        else:
            flags.append(f"Sleep Score {sleep_score}/100 — good")
    elif sleep_hrs:
        if sleep_hrs < 5:
            flags.append(f"Sleep {sleep_hrs}h — insufficient")
            if status == "green":
                status = "amber"
        elif sleep_hrs < 6.5:
            flags.append(f"Sleep {sleep_hrs}h — suboptimal")
        else:
            flags.append(f"Sleep {sleep_hrs}h — adequate")

    # ── Body Battery ──────────────────────────────────────────────────────
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

    # ── Stress ────────────────────────────────────────────────────────────
    if avg_stress is not None:
        if avg_stress > 70:
            flags.append(f"Avg stress {avg_stress}/100 — high")
            if status == "green":
                status = "amber"
        elif avg_stress > 50:
            flags.append(f"Avg stress {avg_stress}/100 — moderate")
        else:
            flags.append(f"Avg stress {avg_stress}/100 — low")

    # Cross-check: if local assessment disagrees strongly with Garmin TR score
    if tr_score is not None:
        if tr_score >= 75 and status == "red":
            flags.append("Note: Garmin Training Readiness is high but local metrics flagged RED — trust local signals")
        elif tr_score < 40 and status == "green":
            flags.append("Note: Garmin Training Readiness is low despite normal local metrics — consider treating as amber")
            status = "amber"

    decisions = {
        "red":   f"REST DAY. No running. Walk only if you need to move.",
        "amber": f"EASY RUN ONLY. 25–30 min, HR under {easy_hr_cap} bpm. No threshold, no intervals.",
        "green": "TRAIN AS PLANNED. You're recovered — execute your next scheduled session.",
    }
    return status, flags, decisions[status]


def assess_training_state(load_data: dict) -> str:
    """
    Coaching call for training load, driven by Garmin's native training status
    + load focus (via classify_load_state). ACWR is appended as a reference
    number only — it no longer triggers the recommendation.
    """
    from garmin.training import classify_load_state

    acwr     = load_data.get("acwr")
    acwr_ref = f" (ACWR {acwr} for reference.)" if acwr is not None else ""
    state    = classify_load_state(load_data.get("training_status"), load_data.get("load_focus"))
    tier, severity = state["tier"], state["severity"]

    if tier == "unknown":
        return (f"Garmin training status unavailable — can't make a load call. "
                f"Build conservatively (max +10%/week).{acwr_ref}")
    if tier == "cutback":
        if severity == "high":
            return f"{state['reason']} Mandatory easy week — cut volume and drop intensity.{acwr_ref}"
        return f"{state['reason']} Back off volume and intensity this week.{acwr_ref}"
    if tier == "add":
        return f"{state['reason']} Room to add volume carefully.{acwr_ref}"
    return f"{state['reason']} Stay the course.{acwr_ref}"
