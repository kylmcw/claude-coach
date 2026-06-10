# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Release

After ANY code change, before marking done:

1. **Bump version** in `manifest.json` (patch = bug fix, minor = new feature)
2. **Backup**: `cp garmin-coach.mcpb backups/garmin-coach_pre-<version>-<desc>_$(date +%Y%m%d).mcpb`  
   Prune to 2 most recent: `ls -t backups/garmin-coach_pre-*.mcpb | tail -n +3 | xargs rm -f`
3. **Rebuild both bundles** (always build both — Kayleigh's bundle gets a patched manifest):
   ```bash
   # Kyle's bundle (base manifest: garmin-coach)
   zip -r /tmp/garmin-coach-new.mcpb . \
     -x "./backups/*" -x "./.DS_Store" -x "./__pycache__/*" -x "./server/__pycache__/*" \
     -x "./garmin-coach.mcpb" -x "./garmin-coach-kayleigh.mcpb" \
     -x "./.git/*" -x "./.gitignore" -x "./.claude/*" -x "./.venv/*"
   cp /tmp/garmin-coach-new.mcpb garmin-coach.mcpb && rm /tmp/garmin-coach-new.mcpb

   # Kayleigh's bundle — same code, name/display_name patched in manifest
   python3 -c "
   import json
   m = json.load(open('manifest.json'))
   m['name'] = 'garmin-coach-kayleigh'
   m['display_name'] = 'Garmin Morning Coach (Kayleigh)'
   json.dump(m, open('manifest.json', 'w'), indent=2)
   "
   zip -r /tmp/garmin-coach-kayleigh-new.mcpb . \
     -x "./backups/*" -x "./.DS_Store" -x "./__pycache__/*" -x "./server/__pycache__/*" \
     -x "./garmin-coach.mcpb" -x "./garmin-coach-kayleigh.mcpb" \
     -x "./.git/*" -x "./.gitignore" -x "./.claude/*" -x "./.venv/*"
   cp /tmp/garmin-coach-kayleigh-new.mcpb garmin-coach-kayleigh.mcpb && rm /tmp/garmin-coach-kayleigh-new.mcpb
   # Restore manifest
   python3 -c "
   import json
   m = json.load(open('manifest.json'))
   m['name'] = 'garmin-coach'
   m['display_name'] = 'Garmin Morning Coach'
   json.dump(m, open('manifest.json', 'w'), indent=2)
   "
   ```
4. **Verify**: `unzip -l garmin-coach.mcpb | grep <changed-file>`
5. **Deploy**: `./deploy.sh` — syncs both extensions into Claude and restarts the app

<!-- Current version is always in manifest.json — never hardcode it here -->

## Smoke Test

```bash
# Prompts for credentials and runs a quick data pull
./run-test.sh

# Import check only (no credentials needed)
./diagnose.sh
```

## Architecture

### Server

- Entry point: `server/main.py` — `app = Server("garmin-coach")` with stdio transport
- `@app.list_tools()` delegates to `get_tool_definitions()` in `tools.py`
- `@app.call_tool()` is a single async if/elif dispatcher — no per-tool decorators
- Each branch calls domain module functions and returns `[types.TextContent(...)]`
- All business logic lives in domain modules, not in `main.py`

### Garmin client

- Lazy singleton: `get_client()` in `garmin/client.py` — creates `Garmin(email, pw)` + `.login()` on first call
- Credentials from env vars `GARMIN_EMAIL` / `GARMIN_PASSWORD` (injected by manifest user_config)
- Client is reused across all tool calls in a session; no requests/httpx — stdlib `urllib` only

### Modules

- `server/main.py` — MCP dispatcher (`call_tool` if/elif chain); no business logic
- `server/tools.py` — tool schema definitions (`get_tool_definitions`)
- `server/utils.py` — shared helpers (`vo2max_to_hm_prediction`, etc.)
- `server/garmin/` — Garmin API layer: client, calibration, readiness, analysis, training, schedule
- `server/coaching/` — coaching logic: plan, thresholds, briefing, race_strategy, recovery_trend, weather
- `server/db/` — SQLite persistence: history (workouts/feedback/coach_log), exercises (registry + aliases + defaults)
- `server/workouts/` — workout creation: steps (builders), workouts (uploaders), validate (PreToolUse hook)

## Garmin API Gotchas

Date keying is inconsistent across endpoints — critical to get right:

| Endpoint | Date arg | Returns |
|---|---|---|
| `get_sleep_data(date)` | **wake date** (not bed date) | last night's sleep |
| `get_hrv_data(today)` | today | last night's overnight HRV |
| `get_heart_rates(today)` | today | today's resting HR |
| `get_body_battery(yesterday, today)` | range | body battery |
| `get_stress_data(yesterday)` | yesterday | stress (today would be incomplete) |

### Calibration baselines

Stored in `~/.garmin-coach.json`, loaded via `load_baselines()` in `garmin/calibration.py`. Auto-created on first use, silently refreshed every 7 days (`RECALIBRATE_AFTER_DAYS`). `assess_readiness()` always calls `load_baselines()` — never use hardcoded values.

Fallback constants (only when < 7 days of data): `HRV_LOW=50`, `HRV_HIGH=70`, `RHR_NORM=60`.

## DB Conventions

### Schema (SQLite at `~/.garmin-coach-history.db`)

- `training_cycles` — one row per race/plan cycle
- `workouts` — planned/completed sessions linked to cycle; `(date, type)` unique index
- `feedback` — RPE/feel/niggles/notes, linked to workout row
- `coach_log` — suggestions; `approved IS NULL` = pending, `1` = accepted, `0` = denied
- `exercise_defaults` — default weight/sets/reps per variation; `name` is unique key
- `garmin_exercises` + `exercise_aliases` — canonical exercise registry with many-to-one alias mapping

### Migration pattern

New columns use `ALTER TABLE ... ADD COLUMN` wrapped in `try/except sqlite3.OperationalError` — idempotent, safe to re-run on existing DBs. Add to the migration loop in `init_history_db`, not to the `CREATE TABLE` statement.

### Connection pattern

Every public function opens its own `sqlite3.connect(HISTORY_DB)` and closes in a `finally` block — no long-lived connections. Use `conn.row_factory = sqlite3.Row` when returning dicts.

### Exercise seeding

`_seed_garmin_exercises(conn)` is idempotent — checks `COUNT(*)` first, skips if > 0. `_SEED_MAP` is source of truth at seed time only; after seeding, the DB is authoritative.

`garmin_category` and `garmin_exercise_name` must be pre-resolved by the caller (`main.py`) via `lookup_garmin_exercise(name)` before calling `set_exercise_defaults` — `db/exercises.py` does not import from `workouts/` (circular import risk).

## Workout Builder

### Running steps (`workouts/steps.py`)

`build_running_steps(steps)` supports: `warmup`/`cooldown` (`duration_minutes`), `easy`/`interval` (`duration_minutes` OR `distance_meters`, optional `target_hr_zone`, `target_hr_low/high`, `target_pace_slow/fast` in sec/km), `recovery` (`duration_minutes`), `repeat` (`iterations` + nested `steps`).

Pace conversion: `speed_m_s = 1000 / pace_sec`. Garmin requires `targetValueOne ≤ targetValueTwo` (slower m/s → faster m/s).

### Strength steps (`workouts/steps.py`)

`build_strength_steps(exercises)` returns `(steps, unmapped_names)`. Each exercise → `RepeatGroup(iterations=sets)` containing a work `ExecutableStep` + rest `ExecutableStep`.

Weight fields on `ExecutableStep` (verified field names):
```python
weight_kwargs["weightValue"] = float(weight_kg)
weight_kwargs["weightUnit"]  = {"unitKey": "kilogram"}
# pass as **weight_kwargs — ConfigDict(extra="allow") lets them through
```

Exercise dict schema: `{name, sets?, reps?, duration_seconds?, rest_seconds?, weight_kg?}`

When `create_and_upload_strength_workout` returns unmapped names, `main.py` warns the user and suggests `set_exercise_defaults` with explicit Garmin mapping.
