import json
from datetime import date, timedelta
from pathlib import Path

from garmin.client import get_client
from utils import get_profile_suffix

# ─── Fallback baselines (used only when calibration has no data) ─────────────
HRV_LOW_DEFAULT  = 50
HRV_HIGH_DEFAULT = 70
RHR_NORM_DEFAULT = 60

# ─── Calibration config ───────────────────────────────────────────────────────
CALIBRATION_FILE = Path.home() / f".garmin-coach{get_profile_suffix()}.json"
RECALIBRATE_AFTER_DAYS  = 7
CALIBRATION_LOOKBACK    = 30


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
