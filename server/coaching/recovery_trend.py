from datetime import date, timedelta

from garmin.client import get_client
from garmin.calibration import load_baselines


def fetch_recovery_trend(days: int = 14) -> list[dict]:
    """
    Fetch day-by-day HRV, RHR, sleep score, body battery, and stress
    for the last `days` days (default 14).
    Returns a list of dicts ordered oldest→newest.
    """
    client  = get_client()
    today   = date.today()
    series  = []

    for d_ago in range(days - 1, -1, -1):
        check_date  = (today - timedelta(days=d_ago)).isoformat()
        prev_date   = (today - timedelta(days=d_ago + 1)).isoformat()
        entry: dict = {"date": check_date}

        # HRV
        try:
            hrv_raw = client.get_hrv_data(check_date)
            if hrv_raw and "hrvSummary" in hrv_raw:
                s = hrv_raw["hrvSummary"]
                entry["hrv"]        = s.get("lastNightAvg")
                entry["hrv_status"] = s.get("status")
        except Exception:
            pass

        # RHR
        try:
            hr_raw = client.get_heart_rates(check_date)
            entry["rhr"] = hr_raw.get("restingHeartRate")
        except Exception:
            pass

        # Sleep score
        try:
            sleep_raw = client.get_sleep_data(check_date)
            if sleep_raw:
                scores = sleep_raw.get("sleepScores") or {}
                dto    = sleep_raw.get("dailySleepDTO") or {}
                score_raw = (scores.get("overall") or scores.get("totalScore")
                             or dto.get("sleepScores", {}).get("overall"))
                # Garmin sometimes returns a dict {"value": 72, ...} instead of a plain int
                if isinstance(score_raw, dict):
                    score = score_raw.get("value") or score_raw.get("score")
                else:
                    score = score_raw
                entry["sleep_score"] = score
                # Total hours as fallback
                start = dto.get("sleepStartTimestampGMT")
                end   = dto.get("sleepEndTimestampGMT")
                if start and end:
                    entry["sleep_hours"] = round((end - start) / 3_600_000, 1)
        except Exception:
            pass

        # Body battery (peak charged value for the day)
        try:
            bb_raw = client.get_body_battery(prev_date, check_date)
            if bb_raw:
                for entry_bb in bb_raw:
                    if isinstance(entry_bb, dict) and "bodyBatteryValuesArray" in entry_bb:
                        vals = [v[1] for v in entry_bb["bodyBatteryValuesArray"] if v[1] is not None]
                        if vals:
                            entry["body_battery"] = max(vals)
        except Exception:
            pass

        # Stress (daily average)
        try:
            stress_raw = client.get_stress_data(check_date)
            if stress_raw and "allStressData" in stress_raw:
                raw = stress_raw["allStressData"].get("stressValuesArray", [])
                levels = [s["stressLevel"] for s in raw if s.get("stressLevel", -1) > 0]
                if levels:
                    entry["avg_stress"] = round(sum(levels) / len(levels))
        except Exception:
            pass

        series.append(entry)

    return series


def _slope(values: list[float]) -> float:
    """Simple linear regression slope over index positions."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den != 0 else 0.0


def analyze_recovery_trend(series: list[dict]) -> dict:
    """
    Compute slopes and detect overreaching/illness flags from a trend series.
    Returns a summary dict with trend direction per metric and alert flags.
    """
    hrv_low, _, rhr_norm = load_baselines()

    hrv_vals = [e["hrv"]          for e in series if isinstance(e.get("hrv"),          (int, float))]
    rhr_vals = [e["rhr"]          for e in series if isinstance(e.get("rhr"),          (int, float))]
    bb_vals  = [e["body_battery"] for e in series if isinstance(e.get("body_battery"), (int, float))]
    ss_vals  = [e["sleep_score"]  for e in series if isinstance(e.get("sleep_score"),  (int, float))]

    def _dir(vals: list[float], positive_is_good: bool = True) -> str:
        if len(vals) < 3:
            return "insufficient data"
        s = _slope(vals)
        threshold = 0.3
        if abs(s) < threshold:
            return "stable"
        rising = s > 0
        if rising == positive_is_good:
            return f"improving (+{abs(s):.1f}/day)"
        else:
            return f"declining ({s:.1f}/day)"

    hrv_dir = _dir(hrv_vals, positive_is_good=True)
    rhr_dir = _dir(rhr_vals, positive_is_good=False)
    bb_dir  = _dir(bb_vals,  positive_is_good=True)
    ss_dir  = _dir(ss_vals,  positive_is_good=True)

    # Overreaching / illness flags
    alerts = []

    # HRV trending down + RHR trending up = classic overreaching signal
    if "declining" in hrv_dir and "declining" in rhr_dir:
        alerts.append(
            "OVERREACHING SIGNAL: HRV falling while RHR rising — "
            "reduce load and prioritise sleep for 3–5 days."
        )

    # Sustained low HRV
    if hrv_vals and len(hrv_vals) >= 5:
        recent_5 = hrv_vals[-5:]
        if all(v < hrv_low for v in recent_5):
            alerts.append(
                f"HRV below personal baseline ({hrv_low}) for 5+ consecutive days — "
                "possible cumulative fatigue or illness onset."
            )

    # RHR elevated vs norm
    if rhr_vals and rhr_vals[-1] > rhr_norm + 7:
        alerts.append(
            f"RHR {rhr_vals[-1]} bpm (+{rhr_vals[-1]-rhr_norm} above norm) — "
            "possible illness, dehydration, or accumulated stress."
        )

    # Body battery consistently depleted
    if bb_vals and len(bb_vals) >= 3:
        if all(v < 40 for v in bb_vals[-3:]):
            alerts.append(
                "Body Battery below 40 for 3+ consecutive days — chronically under-recovering."
            )

    return {
        "hrv_trend": hrv_dir,
        "rhr_trend": rhr_dir,
        "bb_trend":  bb_dir,
        "ss_trend":  ss_dir,
        "hrv_latest": hrv_vals[-1] if hrv_vals else None,
        "rhr_latest": rhr_vals[-1] if rhr_vals else None,
        "alerts":    alerts,
    }


def format_recovery_trend(series: list[dict], analysis: dict, days: int) -> str:
    lines = [
        f"RECOVERY TREND — last {days} days",
        "",
        f"  HRV:          {analysis['hrv_trend']}  (latest: {analysis['hrv_latest'] or 'N/A'})",
        f"  RHR:          {analysis['rhr_trend']}  (latest: {analysis['rhr_latest'] or 'N/A'} bpm)",
        f"  Body Battery: {analysis['bb_trend']}",
        f"  Sleep Score:  {analysis['ss_trend']}",
        "",
        "DAY-BY-DAY:",
        f"  {'Date':<12} {'HRV':>5} {'RHR':>5} {'BB':>5} {'Sleep':>6} {'Stress':>7}",
        f"  {'—'*12} {'—'*5} {'—'*5} {'—'*5} {'—'*6} {'—'*7}",
    ]
    for e in series:
        hrv_s   = str(e["hrv"])          if e.get("hrv")          is not None else "—"
        rhr_s   = str(e["rhr"])          if e.get("rhr")          is not None else "—"
        bb_s    = str(e["body_battery"]) if e.get("body_battery") is not None else "—"
        ss_s    = str(e["sleep_score"])  if e.get("sleep_score")  is not None else "—"
        st_s    = str(e["avg_stress"])   if e.get("avg_stress")   is not None else "—"
        status  = e.get("hrv_status") or ""
        status_tag = f"  ←{status}" if status and status != "BALANCED" else ""
        lines.append(
            f"  {e['date']:<12} {hrv_s:>5} {rhr_s:>5} {bb_s:>5} {ss_s:>6} {st_s:>7}{status_tag}"
        )

    if analysis["alerts"]:
        lines.append("\nALERTS:")
        for a in analysis["alerts"]:
            lines.append(f"  ⚠ {a}")
    else:
        lines.append("\n✓ No overreaching or illness signals detected.")

    return "\n".join(lines)
