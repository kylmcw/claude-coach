# Garmin Morning Coach

## Build & Release

After ANY code change to `server/main.py`, `manifest.json`, `start.sh`, or other project files:

1. **Bump the version** in `manifest.json` (patch bump for bug fixes, minor for new features)
2. **Backup the current mcpb**: `cp garmin-coach.mcpb backups/garmin-coach_pre-<version>-<description>_<date>.mcpb`
   Then prune old backups, keeping only the 2 most recent:
   ```bash
   ls -t backups/garmin-coach_pre-*.mcpb | tail -n +3 | xargs rm -f
   ```
3. **Rebuild the mcpb bundle**:
   ```bash
   zip -r /tmp/garmin-coach-new.mcpb . \
     -x "./backups/*" -x "./.DS_Store" -x "./__pycache__/*" -x "./server/__pycache__/*" -x "./garmin-coach.mcpb"
   cp /tmp/garmin-coach-new.mcpb garmin-coach.mcpb
   rm /tmp/garmin-coach-new.mcpb
   ```
4. **Verify** the new mcpb contains updated files: `unzip -l garmin-coach.mcpb | grep <changed-file>`

Do NOT consider a change complete until the mcpb is rebuilt and the version is bumped.

## Current Version

`1.7.0` — last updated in `manifest.json`.

## Project Structure

- `server/main.py` — MCP server (~1750 lines), all tools, Garmin API logic, readiness scoring, calibration, weather
- `manifest.json` — plugin manifest (version, 11 tool declarations, user config for garmin_email/garmin_password)
- `start.sh` — self-healing startup script that fixes broken venv symlinks, installs deps
- `requirements.txt` — `garminconnect`, `mcp`
- `garmin-coach.mcpb` — distributable plugin bundle (zip of the project)
- `backups/` — pre-change mcpb snapshots
- `diagnose.sh` — import checker; run to verify venv and server startup are clean
- `run-test.sh` — prompts for Garmin credentials and runs `server/main.py --test` for a quick smoke test

## Architecture

### Server Setup
- MCP server using `mcp.server.Server` with stdio transport (`mcp.server.stdio.stdio_server`)
- Entry: `app = Server("garmin-coach")` in `server/main.py`
- Started via `start.sh` → `.venv/bin/python3 server/main.py`

### Auth & Client
- Garmin credentials passed as env vars `GARMIN_EMAIL` / `GARMIN_PASSWORD` (from manifest user_config)
- Lazy-init cached client: `_client = None`, `get_client()` creates `Garmin(email, pw)` + `.login()` on first call
- Client reused across all tool calls within a session

### Tool Registration Pattern
- `@app.list_tools()` — single async handler returning `list[types.Tool]` with JSON Schema `inputSchema`
- `@app.call_tool()` — single async dispatcher `call_tool(name, arguments)` using `if/elif` branches
- Each branch calls a **sync** `fetch_*` function, processes results, returns `[types.TextContent(...)]`
- No per-tool decorators — everything routes through the one dispatcher

### Data Flow
1. Sync `fetch_*()` functions call `garminconnect` library methods (blocking)
2. Processing/scoring functions (e.g., `assess_readiness()`) interpret raw data
3. Dispatcher formats a text summary string and returns it as `TextContent`

## Tools (12 total)

| Tool | Args | Fetcher | Description |
|---|---|---|---|
| `calibrate` | none | `calibrate_baselines()` | Pull 30 days of HRV + RHR from Garmin, compute personal baselines, save to `~/.garmin-coach.json`. Auto-runs on first use and silently every 7 days. |
| `get_morning_metrics` | none | `fetch_morning_data()` | HRV, RHR, sleep, body battery, stress → GREEN/AMBER/RED readiness via `assess_readiness()` |
| `get_training_load` | none | `fetch_training_load()` | Training status + ACWR from last 28 days of activities |
| `get_fitness_trend` | none | `fetch_fitness_trend()` | VO2 Max sampled weekly over 6 weeks |
| `get_recent_activities` | none | `fetch_recent_activities()` | Last 7 days of activities with distance/pace/HR/TE |
| `analyze_run` | `activity_id` (int, opt) | `fetch_run_analysis()` + `analyze_running_form()` | Deep running dynamics analysis: cadence, stride length, ground contact time/balance, vertical oscillation/ratio, power, per-km splits, HR zones, and actionable form recommendations. Auto-selects most recent run if no ID given. |
| `get_weekly_review` | none | `fetch_weekly_summary(0/1)` | This week vs last week: distance, time, sessions, best session, ACWR, coming-week recommendation |
| `get_monthly_review` | none | `fetch_monthly_summary(0/1)` + `fetch_fitness_trend()` | This month vs last month: volume, consistency, longest run, VO2 Max movement, next-month focus |
| `get_run_window` | `location` (str, opt) | `resolve_location()` + `fetch_weather_windows()` | Best time window to run today based on weather. Auto-detects location via IP; accepts named override (e.g. "Belfast") |
| `create_running_workout` | `workout_name` (str), `description` (str, opt), `steps` (array) | direct client call | Creates structured running workout on Garmin Connect |
| `create_strength_workout` | `workout_name` (str), `description` (str, opt), `exercises` (array) | direct client call | Creates strength workout on Garmin Connect |
| `schedule_workout` | `workout_id` (int), `date` (str YYYY-MM-DD) | direct client call | Schedules existing workout to calendar date |

## Calibration & Baselines

Personal baselines are stored in `~/.garmin-coach.json` and loaded at runtime by `load_baselines()`. The file is created automatically on first use and silently refreshed every `RECALIBRATE_AFTER_DAYS` (7) days.

```json
{
  "calibrated_on": "2026-05-14",
  "hrv_low": 75,
  "hrv_high": 95,
  "rhr_norm": 43,
  "hrv_mean": 85.2,
  "rhr_mean": 43.1,
  "hrv_samples": 28,
  "rhr_samples": 30,
  "lookback_days": 30
}
```

Fallback constants (used only when < 7 days of data exist):

```python
HRV_LOW_DEFAULT  = 50
HRV_HIGH_DEFAULT = 70
RHR_NORM_DEFAULT = 60
```

`assess_readiness()` always calls `load_baselines()` — never hardcoded values.

## Imports

```python
import asyncio, json, os
import urllib.parse, urllib.request
from datetime import date, timedelta
from pathlib import Path
from garminconnect import Garmin
from garminconnect.workout import (
    BaseWorkout, RunningWorkout, FitnessEquipmentWorkout,
    WorkoutSegment, ExecutableStep, RepeatGroup, StepType,
    ConditionType, TargetType, create_warmup_step,
    create_cooldown_step, create_interval_step,
    create_recovery_step, create_repeat_group,
)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
```

## Garmin API Date Conventions

- **Sleep**: `get_sleep_data(date)` keys on the **wake date**, not the bed date. Use `today` for last night's sleep.
- **HRV**: `get_hrv_data(today)` returns last night's overnight HRV.
- **Heart Rate / RHR**: `get_heart_rates(today)` returns today's resting HR.
- **Body Battery**: range query `get_body_battery(yesterday, today)`.
- **Stress**: `get_stress_data(yesterday)` returns a full day of data (today would be incomplete in the morning).

## Weather (Open-Meteo)

- `resolve_location(location_str | None)` — if a location string is given, geocodes via `geocoding-api.open-meteo.com`; otherwise falls back to `ip-api.com` for IP-based geolocation.
- `fetch_weather_windows(lat, lon)` — fetches today's hourly forecast from `api.open-meteo.com` (free, no API key). Returns 24-hour array with temperature, feels-like, precipitation probability, wind speed, WMO weather code, UV index.
- `_score_hour(h)` — scores a single hour 0–100 for running suitability.
- `find_best_run_window(hours, is_weekday)` — evaluates candidate windows (weekday: morning + lunch; weekend: 5 windows) and ranks by score.

## Dependencies

- `garminconnect` — Garmin Connect API wrapper (sync calls)
- `mcp` — Model Context Protocol server framework
- `urllib.request` / `urllib.parse` — stdlib; used for Open-Meteo weather + geocoding and ip-api.com geolocation
- No third-party HTTP libraries (requests/httpx/aiohttp)
