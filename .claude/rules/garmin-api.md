---
paths:
  - "server/garmin/readiness.py"
  - "server/garmin/calibration.py"
  - "server/garmin/analysis.py"
  - "server/garmin/training.py"
  - "server/garmin/schedule.py"
  - "server/coaching/briefing.py"
---

# Garmin API Conventions

## Date Keying — Critical Gotchas

- **Sleep**: `get_sleep_data(date)` keys on the **wake date** (not bed date). Use `today` for last night's sleep.
- **HRV**: `get_hrv_data(today)` returns last night's overnight HRV.
- **Heart Rate / RHR**: `get_heart_rates(today)` returns today's resting HR.
- **Body Battery**: range query — `get_body_battery(yesterday, today)`.
- **Stress**: `get_stress_data(yesterday)` — today would be incomplete in the morning.

## Calibration Baselines

Stored in `~/.garmin-coach.json`, loaded at runtime by `load_baselines()` in `calibration.py`.
Auto-created on first use, silently refreshed every 7 days (`RECALIBRATE_AFTER_DAYS`).

```json
{
  "calibrated_on": "2026-05-14",
  "hrv_low": 75, "hrv_high": 95, "rhr_norm": 43,
  "hrv_mean": 85.2, "rhr_mean": 43.1,
  "hrv_samples": 28, "rhr_samples": 30,
  "lookback_days": 30
}
```

Fallback constants (used only when < 7 days of data exist):
```python
HRV_LOW_DEFAULT  = 50
HRV_HIGH_DEFAULT = 70
RHR_NORM_DEFAULT = 60
```

`assess_readiness()` always calls `load_baselines()` — never use hardcoded values.

## Weather (Open-Meteo)

- `resolve_location(str | None)` — geocodes via `geocoding-api.open-meteo.com`; falls back to `ip-api.com`
- `fetch_weather_windows(lat, lon)` — hourly forecast from `api.open-meteo.com` (no API key needed)
- `_score_hour(h)` — 0–100 running suitability score per hour
- `find_best_run_window(hours, is_weekday)` — weekday: morning + lunch; weekend: 5 daylight windows
