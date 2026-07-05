from datetime import date, timedelta

from garmin.client import get_client
from utils import vo2max_to_hm_prediction
from coaching.plan import load_plan


def fetch_training_load() -> dict:
    """
    Training status + ACWR computed from last 28 days of activities.

    Load metric: activityTrainingLoad (Garmin EPOC-based) where available,
    falling back to aerobicTrainingEffect, then distance in km.
    Fetches enough activities to guarantee full 28-day coverage (capped at 60).
    Also surfaces Garmin's native acute/chronic load and load focus where present.
    """
    client = get_client()
    today  = date.today().isoformat()

    training_status = client.get_training_status(today)

    # Fetch enough activities to cover 28 days for athletes doing 2+ sessions/day
    activities = client.get_activities(0, 60)

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

        # Prefer Garmin's EPOC-based training load (most physiologically accurate).
        # Fall back to aerobicTrainingEffect, then distance in km.
        load = (
            act.get("activityTrainingLoad")
            or (act.get("aerobicTrainingEffect") or 0) * 20  # scale TE (1-5) to ~same range as EPOC
            or round((act.get("distance") or 0) / 1000, 1)
        )

        loads_28.append(load)
        if act_date >= cutoff_7:
            loads_7.append(load)

    acute   = round(sum(loads_7), 1)
    chronic = round(sum(loads_28) / 4, 1) if loads_28 else 0  # 28d total → weekly avg
    acwr    = round(acute / chronic, 2) if chronic > 0 else None

    # Pull native Garmin values + load focus from training status
    status_label        = None
    training_load       = None
    garmin_acute_load   = None
    garmin_chronic_load = None
    garmin_load_ratio   = None
    load_focus          = None

    if training_status:
        # Training status structure varies by firmware; try common key paths
        ts = training_status
        if isinstance(ts, list):
            ts = ts[0] if ts else {}
        if not isinstance(ts, dict):
            ts = {}

        status_label  = (ts.get("trainingStatusPhase")
                         or ts.get("latestTrainingStatus"))
        training_load = ts.get("trainingLoad") or ts.get("trainingLoad7Day")

        garmin_acute_load   = ts.get("acuteLoad") or ts.get("acuteTrainingLoad")
        garmin_chronic_load = ts.get("chronicLoad") or ts.get("chronicTrainingLoad")
        garmin_load_ratio   = ts.get("loadRatio") or ts.get("acuteChronicRatio")
        load_focus = ts.get("loadFocus") or ts.get("trainingLoadBalance") or ts.get("loadFocusReason")

        # Real metrics-service response nests the human-readable phrases a couple
        # of levels down, keyed by device id. Dig them out (verified 2026-07-05
        # against a live Forerunner 965). The numeric `trainingStatus` code does
        # NOT match the phrase (saw code 7 with "PRODUCTIVE_6"), so prefer the
        # phrase string and never fall back to the raw int.
        def _first_device(dto_map):
            if isinstance(dto_map, dict):
                for v in dto_map.values():
                    if isinstance(v, dict):
                        return v
            return {}

        status_dev = _first_device((ts.get("mostRecentTrainingStatus") or {})
                                   .get("latestTrainingStatusData"))
        if not status_label:
            status_label = status_dev.get("trainingStatusFeedbackPhrase")
        acute_dto = status_dev.get("acuteTrainingLoadDTO") or {}
        garmin_acute_load   = garmin_acute_load   or acute_dto.get("dailyTrainingLoadAcute")
        garmin_chronic_load = garmin_chronic_load or acute_dto.get("dailyTrainingLoadChronic")
        # Real ACWR ratio is dailyAcuteChronicWorkloadRatio (1.6), NOT acwrPercent
        # (a load-tunnel position %). Confirmed live.
        if garmin_load_ratio is None:
            garmin_load_ratio = acute_dto.get("dailyAcuteChronicWorkloadRatio")

        balance_dev = _first_device((ts.get("mostRecentTrainingLoadBalance") or {})
                                    .get("metricsTrainingLoadBalanceDTOMap"))
        if not load_focus:
            load_focus = balance_dev.get("trainingBalanceFeedbackPhrase")

    # Prefer Garmin native ACWR when available (more accurate as it uses their internal load model)
    effective_acwr = None
    if garmin_load_ratio is not None:
        effective_acwr = round(float(garmin_load_ratio), 2)
    elif acwr is not None:
        effective_acwr = acwr

    return {
        "training_status":    status_label,
        "training_load_7d":   training_load,
        "acute_load":         garmin_acute_load or acute,
        "chronic_load":       garmin_chronic_load or chronic,
        "acwr":               effective_acwr,
        "load_focus":         load_focus,
        "sessions_last_7d":   len(loads_7),
        "sessions_last_28d":  len(loads_28),
        # Computed fallback values (present for transparency when native unavailable)
        "_computed_acute":    acute,
        "_computed_chronic":  chronic,
        "_computed_acwr":     acwr,
    }


def fetch_fitness_trend() -> list[dict]:
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


def fetch_fitness_scores() -> dict:
    """
    Fetch Garmin's Endurance Score and Hill Score for today.
    Returns dict with endurance_score, hill_score, and descriptive levels.
    Both are optional features — returns None values if unavailable.
    """
    client = get_client()
    today  = date.today().isoformat()
    result = {
        "endurance_score": None,
        "endurance_level": None,
        "hill_score":      None,
        "hill_level":      None,
    }

    try:
        es = client.get_endurance_score(startdate=today)
        if es:
            # Response may be a list or dict depending on firmware/version
            entry = es[0] if isinstance(es, list) and es else (es if isinstance(es, dict) else {})
            result["endurance_score"] = (
                entry.get("score") or entry.get("enduranceScore")
                or entry.get("enduranceScoreValue")
            )
            result["endurance_level"] = (
                entry.get("level") or entry.get("enduranceScoreLevel")
            )
    except Exception:
        pass

    try:
        hs = client.get_hill_score(startdate=today)
        if hs:
            entry = hs[0] if isinstance(hs, list) and hs else (hs if isinstance(hs, dict) else {})
            result["hill_score"] = (
                entry.get("score") or entry.get("hillScore")
                or entry.get("hillScoreValue")
            )
            result["hill_level"] = (
                entry.get("level") or entry.get("hillScoreLevel")
            )
    except Exception:
        pass

    return result


def fetch_weekly_zone_distribution(activities: list[dict]) -> dict:
    """
    Aggregate HR-zone time (seconds) across all running activities in the supplied
    activity list (already filtered to a week window).
    Returns {zone: secs, ...} and summary easy_pct / hard_pct for 80/20 analysis.
    Calls get_activity_hr_in_timezones per run — skips on error.
    Note: makes one API call per running activity; a 5-run week = 5 extra calls.
    """
    client = get_client()

    zone_totals: dict[int, float] = {}
    total_secs = 0.0

    for act in activities:
        act_type = (act.get("activityType") or {}).get("typeKey", "")
        if "running" not in act_type and act_type != "trail_running":
            continue
        activity_id = str(act.get("activityId", ""))
        if not activity_id:
            continue
        try:
            hr_zones = client.get_activity_hr_in_timezones(activity_id)
            zone_list = (hr_zones if isinstance(hr_zones, list)
                         else (hr_zones or {}).get("hrTimeInZones") or [])
            for z in zone_list:
                zone_num = z.get("zoneNumber") or z.get("zone")
                secs     = z.get("secsInZone") or 0
                if zone_num is not None:
                    zone_totals[int(zone_num)] = zone_totals.get(int(zone_num), 0) + secs
                    total_secs += secs
        except Exception:
            continue

    if total_secs == 0:
        return {"zones": {}, "easy_pct": None, "moderate_pct": None, "hard_pct": None, "total_min": 0}

    # Zones 1–2 = easy, Zone 3 = moderate/tempo, Zones 4–5 = hard
    easy_secs     = sum(v for k, v in zone_totals.items() if k <= 2)
    moderate_secs = zone_totals.get(3, 0.0)
    hard_secs     = sum(v for k, v in zone_totals.items() if k >= 4)

    return {
        "zones":        {k: round(v / 60, 1) for k, v in sorted(zone_totals.items())},
        "easy_pct":     round(easy_secs / total_secs * 100, 1),
        "moderate_pct": round(moderate_secs / total_secs * 100, 1),
        "hard_pct":     round(hard_secs / total_secs * 100, 1),
        "total_min":    round(total_secs / 60, 1),
    }


def _feedback_review_lines() -> list[str]:
    """Recent RPE/feel/niggle review lines via db.history.assess_recent_feedback (lazy — avoids import cycle)."""
    try:
        from db.history import assess_recent_feedback
        return assess_recent_feedback()["lines"]
    except Exception:
        return []


def fetch_recent_activities(days: int = 7) -> list[dict]:
    """Last `days` days of activities with key running metrics."""
    client  = get_client()
    cutoff  = date.today() - timedelta(days=days)
    recent  = []

    # Page through the feed until we pass the cutoff, so long windows (e.g. backfill_runs
    # over 90 days) aren't truncated by a single fixed-size fetch. For the common short
    # window the first page already crosses the cutoff, so this stays one API call.
    acts: list = []
    start, batch = 0, max(20, days * 2)
    while start < 1000:
        page = client.get_activities(start, batch)
        if not page:
            break
        acts.extend(page)
        oldest = page[-1].get("startTimeLocal", "")[:10]
        try:
            oldest_date = date.fromisoformat(oldest)
        except ValueError:
            break
        if oldest_date < cutoff or len(page) < batch:
            break
        start += batch

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
            "activity_id":                  act.get("activityId"),
            "date":                         act_date_str,
            "name":                         act.get("activityName", "Activity"),
            "type":                         act.get("activityType", {}).get("typeKey", "unknown"),
            "distance_km":                  distance_km,
            "duration_min":                 duration_min,
            "avg_hr":                       avg_hr,
            "avg_pace":                     avg_pace,
            "aerobic_te":                   act.get("aerobicTrainingEffect"),
            "anaerobic_te":                 act.get("anaerobicTrainingEffect"),
            "activity_training_load":       act.get("activityTrainingLoad"),
            "avg_cadence":                  act.get("averageRunningCadenceInStepsPerMinute"),
            "ground_contact_time_ms":       act.get("avgGroundContactTime"),
            "ground_contact_balance_left":  act.get("avgGroundContactBalance"),
            "vertical_oscillation_cm":      act.get("avgVerticalOscillation"),
            "vertical_ratio_pct":           act.get("avgVerticalRatio"),
            "elevation_gain_m":             act.get("elevationGain"),
            "avg_respiration_rate":         act.get("avgRespirationRate"),
        })

    return recent


# ─── Weekly / monthly review helpers ─────────────────────────────────────────

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
    # Store raw activities for zone distribution (only for current week)
    if weeks_back == 0:
        today_str = date.today().isoformat()
        summary["_raw_activities"] = [
            a for a in acts
            if start <= date.fromisoformat(a.get("startTimeLocal", today_str)[:10]) <= min(end, date.today())
        ]
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


def generate_week_suggestions(
    this_week: dict, last_week: dict, load_data: dict
) -> list[dict]:
    """
    Produce 2–3 specific coaching suggestions for the coming week.
    Each dict has: recommendation (str), rationale (str), action_type (str).
    action_type: "adjust_volume" | "add_session" | "add_quality" | "note_only"
    """
    suggestions: list[dict] = []
    acwr     = load_data.get("acwr")
    sessions = this_week.get("session_count", 0)
    vol_this = this_week.get("total_distance_km", 0) or 0
    vol_last = last_week.get("total_distance_km", 0) or 0

    vol_delta_pct = round((vol_this - vol_last) / vol_last * 100, 1) if vol_last > 0 else None

    # Drive the decision off Garmin's native training status + load focus.
    # ACWR is appended to the rationale as a reference number only.
    state = classify_load_state(load_data.get("training_status"), load_data.get("load_focus"))
    tier, severity = state["tier"], state["severity"]
    acwr_ref = f" (ACWR {acwr} shown for reference.)" if acwr is not None else ""

    # ── 1. Volume / load ──────────────────────────────────────────────────
    if tier == "cutback" and severity == "high":
        target = round(vol_this * 0.65, 1)
        suggestions.append({
            "recommendation": f"Mandatory easy week — cap total running at ~{target} km, no intensity.",
            "rationale": f"{state['reason']} Continuing to load raises injury risk significantly.{acwr_ref}",
            "action_type": "adjust_volume",
        })
    elif tier == "cutback":
        target = round(vol_this * 0.80, 1)
        suggestions.append({
            "recommendation": f"Back off — aim for ~{target} km at easy effort only this week.",
            "rationale": f"{state['reason']} A lighter week lets you absorb load without accumulating fatigue.{acwr_ref}",
            "action_type": "adjust_volume",
        })
    elif tier == "add" and vol_delta_pct is not None and vol_delta_pct < -20:
        target = round(vol_this * 1.10, 1)
        suggestions.append({
            "recommendation": f"Safe to rebuild — target ~{target} km, adding one easy session back in.",
            "rationale": f"Volume dropped {abs(vol_delta_pct):.0f}%. {state['reason']} The body has absorbed the load; time to progress.{acwr_ref}",
            "action_type": "add_session",
        })
    elif tier == "add":
        base = vol_this or vol_last or 30
        target = round(base * 1.10, 1)
        suggestions.append({
            "recommendation": f"Underloading — room to add ~10% volume or an extra easy run (target ~{target} km).",
            "rationale": f"{state['reason']} You have capacity to absorb more load safely.{acwr_ref}",
            "action_type": "add_session",
        })
    elif tier == "unknown":
        suggestions.append({
            "recommendation": "Build gradually — no Garmin training-status signal yet, so increase volume by no more than 10% per week.",
            "rationale": f"{state['reason']} Conservative progression is the safest default.{acwr_ref}",
            "action_type": "adjust_volume",
        })
    elif vol_delta_pct is not None and vol_delta_pct > 15:
        suggestions.append({
            "recommendation": "Hold volume steady this week before building further.",
            "rationale": f"Volume jumped {vol_delta_pct:+.0f}% last week. {state['reason']} Let connective tissue catch up before adding more.{acwr_ref}",
            "action_type": "adjust_volume",
        })
    else:  # hold
        suggestions.append({
            "recommendation": "Load is productive — execute your planned block as written.",
            "rationale": f"{state['reason']} No adjustment needed.{acwr_ref}",
            "action_type": "note_only",
        })

    # ── 2. Consistency ────────────────────────────────────────────────────
    if sessions < 3:
        suggestions.append({
            "recommendation": f"Prioritise 3 consistent sessions before adding quality work.",
            "rationale": f"Only {sessions} session{'s' if sessions != 1 else ''} logged this week. Consistency beats intensity at this stage.",
            "action_type": "add_session",
        })
    elif tier == "hold":
        # Productive load + consistent sessions — suggest one quality session
        suggestions.append({
            "recommendation": "Include one threshold session this week: 20–30 min at comfortably hard effort (RPE 7–8).",
            "rationale": "Load is productive and base is consistent. One quality session per week is enough stimulus to drive aerobic adaptation.",
            "action_type": "add_quality",
        })

    return suggestions[:3]


def _week_recommendation(load_data: dict, vol_delta_pct: float | None) -> str:
    """
    Plain-English recommendation for the coming week, driven by Garmin's native
    training status + load focus (not the raw ACWR ratio). ACWR is shown as a
    reference figure elsewhere in the review.
    """
    state = classify_load_state(load_data.get("training_status"), load_data.get("load_focus"))
    tier, severity = state["tier"], state["severity"]

    if tier == "unknown":
        return f"Not enough Garmin training-status data for a load call — build gradually. ({state['reason']})"
    if tier == "cutback":
        if severity == "high":
            return f"MANDATORY EASY WEEK. {state['reason']} Cut volume 30–40%, no intensity."
        return f"Back off. {state['reason']} Reduce volume ~20% and keep effort easy."
    if tier == "add":
        if vol_delta_pct is not None and vol_delta_pct < -20:
            return f"Volume dropped significantly. Safe to rebuild — add ~10% this week. ({state['reason']})"
        return f"Room to build. {state['reason']} Add ~10% volume or one extra easy session."
    # hold
    if vol_delta_pct is not None and vol_delta_pct > 15:
        return f"Big volume jump this week. Hold steady next week before building again. ({state['reason']})"
    return f"Stay the course — execute your next planned block. ({state['reason']})"


def _delta_str(a, b, unit="", decimals=1) -> str:
    if a is None or b is None:
        return "N/A"
    d = round(a - b, decimals)
    return f"{'+' if d >= 0 else ''}{d}{unit}"


def _fmt_load_focus(focus: str | None) -> str:
    if not focus:
        return ""
    labels = {
        "LOW_AEROBIC_DEFICIT":    "Low aerobic deficit — add easy volume",
        "LOW_AEROBIC_SURPLUS":    "Low aerobic surplus",
        "HIGH_AEROBIC_DEFICIT":   "High aerobic deficit",
        "HIGH_AEROBIC_SURPLUS":   "High aerobic surplus — room to add quality",
        "ANAEROBIC_SURPLUS":      "Anaerobic surplus — back off hard sessions",
        "ANAEROBIC_DEFICIT":      "Anaerobic deficit",
        "OPTIMAL":                "Load balanced — optimal distribution",
    }
    return labels.get(focus.upper(), focus)


def classify_load_state(training_status: str | None, load_focus: str | None) -> dict:
    """
    Map Garmin's native training status + load-focus phrases onto a coaching
    action tier. This is what drives cut-back / hold / add-volume decisions —
    the raw ACWR ratio is now shown as a reference number only.

    Returns {"tier", "severity", "reason"}:
      tier     — "cutback" | "hold" | "add" | "unknown"
      severity — "high" | "moderate" | None (only meaningful for cutback/add)

    Matches on keywords, not exact enums: Garmin suffixes its phrases
    ("PRODUCTIVE_6", "OVERREACHING_2", "AEROBIC_LOW_SHORTAGE") and the set
    varies by firmware. Verified 2026-07-05 against a live Forerunner 965:
    status "PRODUCTIVE_6", load focus "AEROBIC_LOW_FOCUS".
    """
    ts = (training_status or "").upper()
    lf = (load_focus or "").upper()

    if not ts and not lf:
        return {"tier": "unknown", "severity": None,
                "reason": "Garmin returned no training status or load focus (insufficient data)."}

    # Load-focus signals. Note "ANAEROBIC" contains "AEROBIC" as a substring,
    # so detect anaerobic first and exclude it from the aerobic check.
    anaerobic = "ANAEROBIC" in lf
    aerobic   = ("AEROBIC" in lf) and not anaerobic
    anaerobic_excess = anaerobic and ("SURPLUS" in lf or "EXCESS" in lf or "FOCUS" in lf)
    aerobic_shortage = aerobic and ("SHORTAGE" in lf or "DEFICIT" in lf)

    # ── Training status = total-load trend → primary volume tier ──
    # Check UNPRODUCTIVE before PRODUCTIVE (substring overlap).
    if "STRAINED" in ts:
        return {"tier": "cutback", "severity": "high",
                "reason": f"Training status '{training_status}' — sustained overload with fitness declining."}
    if "OVERREACH" in ts or "UNPRODUCTIVE" in ts:
        return {"tier": "cutback", "severity": "moderate",
                "reason": f"Training status '{training_status}' — load is outpacing recovery."}
    if "DETRAINING" in ts:
        return {"tier": "add", "severity": "moderate",
                "reason": f"Training status '{training_status}' — load has dropped and fitness is fading."}

    productive = ("PRODUCTIVE" in ts or "PEAKING" in ts
                  or "MAINTAINING" in ts or "RECOVERY" in ts)
    if productive:
        if anaerobic_excess:
            return {"tier": "cutback", "severity": "moderate",
                    "reason": f"Status '{training_status}' but load focus '{load_focus}' — "
                              "excess anaerobic load, cut the hard sessions."}
        return {"tier": "hold", "severity": None,
                "reason": f"Training status '{training_status}' — productive, absorbing load well."}

    # No usable status phrase (e.g. NO_STATUS, or only load focus present) —
    # lean on load focus alone.
    if anaerobic_excess:
        return {"tier": "cutback", "severity": "moderate",
                "reason": f"Load focus '{load_focus}' — excess anaerobic load, back off hard sessions."}
    if aerobic_shortage:
        return {"tier": "add", "severity": "moderate",
                "reason": f"Load focus '{load_focus}' — aerobic base is short, room to add easy volume."}

    return {"tier": "unknown", "severity": None,
            "reason": f"Training status '{training_status or 'N/A'}' / load focus "
                      f"'{load_focus or 'N/A'}' — no clear load signal."}


def format_weekly_review(this_week: dict, last_week: dict, load_data: dict, plan_context: str = "") -> str:
    vol_delta_pct = None
    if last_week["total_distance_km"] > 0:
        vol_delta_pct = round(
            (this_week["total_distance_km"] - last_week["total_distance_km"])
            / last_week["total_distance_km"] * 100, 1
        )

    recommendation = _week_recommendation(load_data, vol_delta_pct)

    def fmt_types(tc: dict) -> str:
        return ", ".join(f"{k} x{v}" for k, v in sorted(tc.items())) or "none"

    lines = [
        f"WEEKLY REVIEW — w/c {this_week['start']}",
        "",
        f"{'Metric':<28} {'This week':>12} {'Last week':>12} {'Delta':>10}",
        f"{'-'*28} {'-'*12} {'-'*12} {'-'*10}",
        f"{'Sessions':<28} {this_week['session_count']:>12} {last_week['session_count']:>12} "
        f"{_delta_str(this_week['session_count'], last_week['session_count'], '', 0):>10}",
        f"{'Total distance (km)':<28} {this_week['total_distance_km']:>12} {last_week['total_distance_km']:>12} "
        f"{_delta_str(this_week['total_distance_km'], last_week['total_distance_km'], 'km'):>10}",
        f"{'Running distance (km)':<28} {this_week['run_distance_km']:>12} {last_week['run_distance_km']:>12} "
        f"{_delta_str(this_week['run_distance_km'], last_week['run_distance_km'], 'km'):>10}",
        f"{'Longest run (km)':<28} {this_week['longest_run_km']:>12} {last_week['longest_run_km']:>12} "
        f"{_delta_str(this_week['longest_run_km'], last_week['longest_run_km'], 'km'):>10}",
        f"{'Total time (min)':<28} {this_week['total_duration_min']:>12} {last_week['total_duration_min']:>12} "
        f"{_delta_str(this_week['total_duration_min'], last_week['total_duration_min'], 'min'):>10}",
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

    lines.append(f"\nACWR (reference only — training status governs, not a 0.8–1.3 gate): "
                 f"{load_data['acwr'] or 'N/A'}  |  "
                 f"Acute load: {load_data['acute_load']}  |  "
                 f"Chronic load: {load_data['chronic_load']}")

    if load_data.get("load_focus"):
        lines.append(f"Load focus: {_fmt_load_focus(load_data['load_focus'])}")

    # ── Intensity distribution ─────────────────────────────────────────────
    raw_acts = this_week.get("_raw_activities") or []
    if raw_acts:
        try:
            zones_dist   = fetch_weekly_zone_distribution(raw_acts)
            easy_pct     = zones_dist.get("easy_pct")
            moderate_pct = zones_dist.get("moderate_pct")
            hard_pct     = zones_dist.get("hard_pct")
            if easy_pct is not None:
                zones_str = "  ".join(
                    f"Z{z}: {m}min" for z, m in zones_dist["zones"].items()
                )
                _plan      = load_plan()
                methodology = (_plan or {}).get("methodology", "polarized")
                lines.append(f"\nINTENSITY DISTRIBUTION (this week):")
                if methodology == "pyramidal":
                    lines.append(
                        f"  Easy (Z1-2): {easy_pct}%   Moderate (Z3): {moderate_pct}%   Hard (Z4-5): {hard_pct}%"
                    )
                    if zones_str:
                        lines.append(f"  Zones: {zones_str}")
                    # Pyramidal targets: ~70% easy, ~20% moderate, ~10% hard
                    if easy_pct < 60:
                        lines.append(
                            f"  Only {easy_pct}% easy (target >=70%). "
                            "Too much time in moderate/hard zones — back off easy days."
                        )
                    elif hard_pct > 20:
                        lines.append(
                            f"  Hard zone at {hard_pct}% (target <=10%). "
                            "Reduce back-to-back intense sessions."
                        )
                    else:
                        lines.append(
                            f"  Good pyramidal distribution — {easy_pct}% easy / {moderate_pct}% moderate / {hard_pct}% hard."
                        )
                else:
                    # Polarized 80/20
                    lines.append(f"  Easy (Z1-2): {easy_pct}%   Hard (Z3-5): {hard_pct}%")
                    if zones_str:
                        lines.append(f"  Zones: {zones_str}")
                    if easy_pct < 70:
                        lines.append(
                            f"  Only {easy_pct}% easy (target >=80%). "
                            "You're running your easy days too hard — slow them down."
                        )
                    elif easy_pct >= 80:
                        lines.append(f"  Good polarisation — {easy_pct}% easy aligns with 80/20 target.")
        except Exception:
            pass

    # ── Logged feedback scan (niggles + RPE/feel) ─────────────────────────
    fb_lines = _feedback_review_lines()
    if fb_lines:
        lines.append("\nLOGGED FEEDBACK (last 14d):")
        for w in fb_lines:
            lines.append(f"  {w}")

    lines.append(f"\nNEXT WEEK: {recommendation}")

    lines.append(plan_context)
    return "\n".join(lines)


def format_monthly_review(this_month: dict, last_month: dict, trend: list, plan_context: str = "") -> str:
    vo2_values = [e["vo2max"] for e in trend if e["vo2max"] is not None]
    vo2_start  = vo2_values[0]  if vo2_values else None
    vo2_latest = vo2_values[-1] if vo2_values else None
    vo2_delta  = round(vo2_latest - vo2_start, 1) if (vo2_start and vo2_latest) else None

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
        f"{'Metric':<30} {this_month['month_label'][:12]:>14} {last_month['month_label'][:12]:>14} {'Delta':>10}",
        f"{'-'*30} {'-'*14} {'-'*14} {'-'*10}",
        f"{'Sessions':<30} {this_month['session_count']:>14} {last_month['session_count']:>14} "
        f"{_delta_str(this_month['session_count'], last_month['session_count'], '', 0):>10}",
        f"{'Total distance (km)':<30} {this_month['total_distance_km']:>14} {last_month['total_distance_km']:>14} "
        f"{_delta_str(this_month['total_distance_km'], last_month['total_distance_km'], 'km'):>10}",
        f"{'Running distance (km)':<30} {this_month['run_distance_km']:>14} {last_month['run_distance_km']:>14} "
        f"{_delta_str(this_month['run_distance_km'], last_month['run_distance_km'], 'km'):>10}",
        f"{'Longest run (km)':<30} {this_month['longest_run_km']:>14} {last_month['longest_run_km']:>14} "
        f"{_delta_str(this_month['longest_run_km'], last_month['longest_run_km'], 'km'):>10}",
        f"{'Total time (min)':<30} {this_month['total_duration_min']:>14} {last_month['total_duration_min']:>14} "
        f"{_delta_str(this_month['total_duration_min'], last_month['total_duration_min'], 'min'):>10}",
    ]

    lines.append(f"\nConsistency this month:  {consistency_str}")
    lines.append(f"Consistency last month:  {last_consistency_str}")

    if vo2_latest is not None:
        vo2_line = f"\nVO2 Max (6-week window): {vo2_latest}"
        if vo2_delta is not None:
            direction = "up" if vo2_delta > 0.5 else "down" if vo2_delta < -0.5 else "stable"
            vo2_line += f"  ({direction} {'+' if vo2_delta >= 0 else ''}{vo2_delta} over 6 weeks)"
        lines.append(vo2_line)

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

    # ── Logged feedback scan (niggles + RPE/feel) ─────────────────────────
    fb_lines = _feedback_review_lines()
    if fb_lines:
        lines.append("\nLOGGED FEEDBACK (last 14d):")
        for w in fb_lines:
            lines.append(f"  {w}")

    lines.append(f"\nNEXT MONTH FOCUS: {focus}")

    lines.append(plan_context)
    return "\n".join(lines)


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


def fetch_strength_exercise_sets(activity_id: int | None = None) -> dict:
    """
    Fetch and normalize exercise sets from a completed fitness_equipment activity.

    Returns:
      {
        "activity_id": int,
        "activity_name": str,
        "date": str,               # YYYY-MM-DD
        "exercises": [
          {
            "name": str,           # display name (title-cased from Garmin)
            "category": str,       # Garmin category key, e.g. "BENCH_PRESS"
            "exercise_name": str,  # Garmin exercise key, e.g. "BARBELL_BENCH_PRESS"
            "sets_completed": int,
            "sets": [{"reps": int, "weight_kg": float | None}],
            "weight_kg": float | None,  # modal weight across sets
          }
        ],
        "error": str | None,
      }
    """
    from datetime import timedelta as _td
    client = get_client()

    if activity_id is None:
        start = (date.today() - _td(days=30)).isoformat()
        acts  = client.get_activities_by_date(start, activitytype="fitness_equipment")
        if not acts:
            return {"error": "No fitness_equipment activities found in the last 30 days.",
                    "exercises": []}
        act         = acts[0]
        activity_id = int(act["activityId"])
    else:
        try:
            act = client.get_activity(str(activity_id))
        except Exception:
            act = {}

    raw        = client.get_activity_exercise_sets(activity_id)
    set_list   = raw if isinstance(raw, list) else raw.get("exerciseSets", [])

    exercises: dict[str, dict] = {}

    for s in set_list:
        set_type = (s.get("setType") or "").upper()
        if set_type == "REST":
            continue

        # Extract exercise identity — Garmin uses two different shapes
        ex_list  = s.get("exercises") or []
        if ex_list and isinstance(ex_list[0], dict):
            ex_info   = ex_list[0]
            category  = ex_info.get("category") or ex_info.get("exerciseCategory") or ""
            ex_name   = (ex_info.get("name") or ex_info.get("exerciseName")
                         or ex_info.get("exerciseKey") or "")
        else:
            category  = s.get("category") or s.get("exerciseCategory") or ""
            ex_name   = s.get("exerciseName") or s.get("exercise") or s.get("name") or ""

        key      = f"{category}:{ex_name}" if (category or ex_name) else "unknown"
        display  = ex_name.replace("_", " ").title() if ex_name else "Unknown Exercise"

        reps   = int(s.get("repetitionCount") or s.get("reps") or 0)
        weight = s.get("weight") or s.get("weightValue")

        # Normalise weight to kg.
        # Garmin's exercise-set API returns weightValue in grams regardless
        # of the unitKey display preference (unitKey only affects how the
        # Connect app *displays* the number, not the raw stored value).
        # Confirmed empirically against 2026-06-28 data: raw 70000 / 65000 /
        # 80000 match the 70kg/65kg/70-80kg loads actually programmed and
        # logged for that session. Previously this code left kilogram-unit
        # entries un-divided (saving e.g. 70000kg as a bench default) and
        # additionally mis-treated pound-unit entries as if the raw value
        # were already in pounds rather than grams.
        if weight:
            weight = round(float(weight) / 1000, 2)

        if key not in exercises:
            exercises[key] = {
                "name":          display,
                "category":      category,
                "exercise_name": ex_name,
                "sets":          [],
            }
        exercises[key]["sets"].append({"reps": reps, "weight_kg": weight})

    result_exercises = []
    for ex in exercises.values():
        sets    = ex["sets"]
        weights = [s["weight_kg"] for s in sets if s["weight_kg"] is not None]
        modal   = max(set(weights), key=weights.count) if weights else None
        result_exercises.append({
            "name":           ex["name"],
            "category":       ex["category"],
            "exercise_name":  ex["exercise_name"],
            "sets_completed": len(sets),
            "sets":           sets,
            "weight_kg":      modal,
        })

    return {
        "activity_id":   activity_id,
        "activity_name": act.get("activityName", "Strength Workout"),
        "date":          (act.get("startTimeLocal") or act.get("startTimeGMT") or "")[:10],
        "exercises":     result_exercises,
        "error":         None,
    }
