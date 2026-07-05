import os


def get_profile_suffix() -> str:
    """Filename suffix for the active coaching profile (e.g. '-kayleigh', or '' for default).

    `GARMIN_COACH_PROFILE` is fed from an *optional* manifest user_config field. When the
    user leaves it blank, the host can pass the unexpanded literal `${user_config.profile_name}`
    instead of an empty string — which previously produced stray `.garmin-coach-${...}` files.
    Treat empty *or* an unexpanded `${...}` placeholder as the default profile (no suffix).
    """
    profile = os.environ.get("GARMIN_COACH_PROFILE", "").strip()
    if not profile or profile.startswith("${"):
        return ""
    return f"-{profile}"


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
