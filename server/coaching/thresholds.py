from garmin.client import get_client


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
            return {"lthr": lthr, "lt_pace_sec": lt_pace_sec, "source": "garmin_lt"}
    except Exception:
        pass

    return {"lthr": None, "lt_pace_sec": None, "source": "unavailable"}


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
    zones = derive_zones(lt.get("lthr"), lt.get("lt_pace_sec"))
    zones["_source"]      = lt["source"]
    zones["_lthr"]        = lt.get("lthr")
    zones["_lt_pace_sec"] = lt.get("lt_pace_sec")
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
