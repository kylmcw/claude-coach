import statistics
from collections.abc import Sequence
from datetime import date, timedelta

from garmin.client import get_client
from garmin.calibration import read_state, update_state

# ─── Lap-derived LT pace config ──────────────────────────────────────────────
# The 1000m floor is load-bearing, not fussiness. Shorter laps are either recovery
# jogs (HR lags effort by 30-60s, so they carry the previous rep's HR at a jog
# pace) or sub-threshold-duration reps run at VO2max pace. Both land inside the
# threshold HR band while being nothing like threshold efforts.
LT_LAP_MIN_DISTANCE_M  = 1000
LT_LAP_MIN_SAMPLES     = 5
LT_LAP_LOOKBACK_DAYS   = 90
LT_LAP_ACTIVITY_LIMIT  = 40
LT_REDERIVE_AFTER_DAYS = 7


def fetch_lactate_threshold() -> dict:
    """
    Fetch LT heart rate and pace from Garmin's latestLactateThreshold endpoint.
    Returns dict with lthr (bpm), lt_pace_sec (sec/km), source.
    """
    try:
        client = get_client()
        raw = client.get_lactate_threshold(latest=True)
        shr = (raw or {}).get("speed_and_heart_rate") or {}
        speed_raw = shr.get("speed")
        lthr = shr.get("heartRate") or shr.get("hearRate")  # handle Garmin's historical typo

        # Garmin's /latestLactateThreshold returns speed in sec/m (pace), not m/s.
        # Convert: sec/m × 1000 m/km = sec/km.
        lt_pace_sec = round(speed_raw * 1000) if speed_raw and speed_raw > 0 else None

        if lthr or lt_pace_sec:
            # Garmin only re-detects LT during a hard enough effort, so this can be
            # months old while still being served as current. Callers must show the age.
            measured_on = (shr.get("calendarDate") or "")[:10] or None
            return {"lthr": lthr, "lt_pace_sec": lt_pace_sec,
                    "measured_on": measured_on, "source": "garmin_lt"}
    except Exception:
        pass

    return {"lthr": None, "lt_pace_sec": None, "measured_on": None,
            "source": "unavailable"}


def median_threshold_pace(
    laps: Sequence[tuple[float, float, float]],
    lthr: int | None,
    min_distance_m: int = LT_LAP_MIN_DISTANCE_M,
    min_samples: int = LT_LAP_MIN_SAMPLES,
) -> int | None:
    """
    Median pace (sec/km) of laps run at 93–103% LTHR.

    laps: (distance_m, pace_sec_per_km, avg_hr) tuples.

    Median, not mean — one mis-tagged lap shouldn't move the anchor that every
    prescribed pace hangs off. Returns None below min_samples so the caller falls
    back rather than trusting a number built from two laps.
    """
    if not lthr:
        return None

    low, high = lthr * 0.93, lthr * 1.03
    paces = [
        pace for dist, pace, hr in laps
        if dist >= min_distance_m and low <= hr <= high
    ]
    if len(paces) < min_samples:
        return None
    return round(statistics.median(paces))


def fetch_threshold_laps(
    lookback_days: int = LT_LAP_LOOKBACK_DAYS,
    activity_limit: int = LT_LAP_ACTIVITY_LIMIT,
) -> list[tuple[float, float, float]]:
    """
    Pull (distance_m, pace_sec_per_km, avg_hr) for every lap of recent runs.

    ponytail: one splits call per activity, sequential. ~40 calls on a cold cache;
    fine because resolve_lt_pace only re-derives weekly. Parallelise if it ever
    lands in an interactive path.
    """
    client = get_client()
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    laps: list[tuple[float, float, float]] = []

    for act in client.get_activities(0, activity_limit):
        if "running" not in (act.get("activityType") or {}).get("typeKey", ""):
            continue
        if (act.get("startTimeLocal") or "")[:10] < cutoff:
            continue
        try:
            splits = client.get_activity_splits(act["activityId"]).get("lapDTOs", [])
        except Exception:
            continue
        for lap in splits:
            speed = lap.get("averageSpeed")
            hr    = lap.get("averageHR")
            dist  = lap.get("distance", 0)
            if speed and hr and dist:
                laps.append((dist, 1000 / speed, hr))

    return laps


def resolve_lt_pace(lthr: int | None, garmin_pace: int | None) -> tuple[int | None, str]:
    """
    Pick the LT pace to build zones from. Returns (pace_sec_per_km, source).

    Priority:
      1. manual override (`lt_pace_sec` in the calibration file) — never expires
      2. lap-derived median, cached and re-derived every LT_REDERIVE_AFTER_DAYS
      3. Garmin's stored value

    All three are the same quantity in the same units, so callers treat them
    interchangeably — `source` is for display, never for branching.
    """
    state = read_state()

    manual = state.get("lt_pace_sec")
    if manual:
        return int(manual), "manual"

    cached, cached_on = state.get("lt_pace_derived"), state.get("lt_pace_derived_on")
    if cached and cached_on:
        try:
            if (date.today() - date.fromisoformat(cached_on)).days < LT_REDERIVE_AFTER_DAYS:
                return int(cached), "laps"
        except ValueError:
            pass  # corrupt cache date → re-derive

    try:
        derived = median_threshold_pace(fetch_threshold_laps(), lthr)
    except Exception:
        derived = None

    if derived:
        update_state(lt_pace_derived=derived,
                     lt_pace_derived_on=date.today().isoformat())
        return derived, "laps"

    return garmin_pace, "garmin_lt"


def derive_zones(lthr: int | None, lt_pace_sec: int | None) -> dict:
    """
    Derive training zones from LTHR and LT threshold pace.

    Pace zones (all sec/km):
      easy:      LT+45 → LT+90     (conversational, Z1-2)
      aerobic:   LT+20 → LT+45     (steady-state, Z2-3)
      threshold: LT-5  → LT+10     (comfortably hard, Z4)
      interval:  LT-30 → LT-5      (hard, VO2max, Z5)

    HR zones (bpm, derived from LTHR using Friel running coefficients):
      easy:      70–84% LTHR
      aerobic:   84–93% LTHR
      threshold: 93–103% LTHR
      interval:  103–112% LTHR
    """
    zones: dict[str, dict] = {
        "easy":      {},
        "aerobic":   {},
        "threshold": {},
        "interval":  {},
    }

    if lthr:
        zones["easy"]["hr"]      = (round(lthr * 0.70), round(lthr * 0.84))
        zones["aerobic"]["hr"]   = (round(lthr * 0.84), round(lthr * 0.93))
        zones["threshold"]["hr"] = (round(lthr * 0.93), round(lthr * 1.03))
        zones["interval"]["hr"]  = (round(lthr * 1.03), round(lthr * 1.12))
    else:
        for z in zones:
            zones[z]["hr"] = None

    if lt_pace_sec:
        zones["easy"]["pace"]      = (lt_pace_sec + 45, lt_pace_sec + 90)
        zones["aerobic"]["pace"]   = (lt_pace_sec + 20, lt_pace_sec + 45)
        zones["threshold"]["pace"] = (lt_pace_sec - 5,  lt_pace_sec + 10)
        zones["interval"]["pace"]  = (lt_pace_sec - 30, lt_pace_sec - 5)
    else:
        for z in zones:
            zones[z]["pace"] = None

    return zones


def get_zones() -> dict:
    """
    Top-level helper: fetch LT from Garmin then derive all pace/HR zones.
    Attaches _source, _lthr, _lt_pace_sec for caller transparency.
    """
    lt = fetch_lactate_threshold()

    # No LT data at all means no LTHR, and without an LTHR there's no HR band to
    # select threshold laps with — lap derivation can't run. Don't let a stale cache
    # populate pace zones while the source says unavailable.
    if lt["source"] == "unavailable":
        lt_pace, pace_source = None, "unavailable"
    else:
        lt_pace, pace_source = resolve_lt_pace(lt.get("lthr"), lt.get("lt_pace_sec"))

    zones = derive_zones(lt.get("lthr"), lt_pace)
    zones["_source"]          = lt["source"]
    zones["_lthr"]            = lt.get("lthr")
    zones["_lt_pace_sec"]     = lt_pace
    zones["_pace_source"]     = pace_source
    zones["_measured_on"]     = lt.get("measured_on")
    zones["_garmin_pace_sec"] = lt.get("lt_pace_sec")
    return zones


def fmt_pace(sec_per_km: int | None) -> str:
    """Format seconds/km as 'M:SS/km'."""
    if sec_per_km is None:
        return "N/A"
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def easy_hr_ceiling(zones: dict, fallback: int = 152) -> int:
    """
    Return the upper bound of the easy HR zone.
    Used to replace the hardcoded 155bpm amber-day cap with a personalized figure.
    """
    hr = zones.get("easy", {}).get("hr")
    if hr and hr[1]:
        return int(hr[1])
    return fallback
