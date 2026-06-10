import json
import urllib.parse
import urllib.request


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
