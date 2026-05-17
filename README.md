# Garmin Morning Coach

A Claude plugin that connects Claude to your Garmin Connect account. Pull your daily health metrics, training load, and fitness trends — and push structured workouts directly to your Garmin watch — all through natural conversation.

## Features

Fourteen tools are available to Claude:

**Daily coaching**
- **calibrate** — Computes your personal HRV and resting HR baselines from your last 30 days of Garmin data. Runs automatically the first time you use the plugin, and silently refreshes every 7 days so your baselines stay current as your fitness changes.
- **get_morning_metrics** — Daily readiness check: HRV, resting HR, sleep, body battery, and stress, scored against *your* personal baselines and returned as a GREEN / AMBER / RED decision with plain-English coaching. Includes training plan context if a plan is set up.
- **get_training_load** — Training status, 7-day and 28-day load, and acute-to-chronic workload ratio (ACWR) to flag overreaching and injury risk. Includes training plan context if a plan is set up.
- **get_fitness_trend** — VO2 Max sampled weekly over the last 6 weeks so you can see whether fitness is trending up, stable, or declining.
- **get_recent_activities** — Your last 7 days of activities with distance, pace, duration, heart rate, and aerobic/anaerobic training effect scores.
- **get_weekly_review** — This week vs last week: total distance, time, sessions, running distance, longest run, best session, and ACWR — with a specific recommendation for the coming week. Includes training plan context if a plan is set up.
- **get_monthly_review** — This month vs last month: volume, consistency (weeks with 3+ sessions), longest run, VO2 Max movement, and a focus area for next month. Includes training plan context if a plan is set up.
- **get_run_window** — Checks today's weather and recommends the best time to run. On weekdays evaluates morning (6–9am) and lunch (12–1pm) slots. On weekends evaluates all daylight windows. Auto-detects your location, or accepts a named place (e.g. "Belfast").

**Training plan**
- **setup_training_plan** — Set up an overarching race training plan. Call with no arguments to pull your current Garmin race predictions and receive a questionnaire. Call again with your answers to create the plan. Once set, all daily coaching tools automatically include plan context (current week, phase, race countdown, phase-specific tip). Uses Garmin's built-in Race Predictor for time estimates; falls back to VO2 Max table interpolation if unavailable.
- **get_plan_status** — Full plan breakdown: race details, countdown, current week and phase, predicted vs target time, weekly structure, and a phase-by-phase table with your current position marked.
- **clear_training_plan** — Deletes the current plan. Requires `confirmation=true`. Use when starting a new training cycle.

**Workouts**
- **create_running_workout** — Build a structured running workout (warmup, easy, intervals, recovery, cooldown, repeats) and upload it to Garmin Connect.
- **create_strength_workout** — Build a structured strength workout from a library of 70+ mapped exercises and upload it to Garmin Connect.
- **schedule_workout** — Pin any uploaded workout to a specific date on your Garmin calendar so it appears on your watch that day.

## Prerequisites

- **Python 3.10+** (3.14 preferred)
- **A Garmin Connect account** with email/password login enabled
- **Claude desktop app** with plugin support (Cowork mode or Claude Code)

## Installation

1. **Get the plugin file.** You need the `garmin-coach.mcpb` file — this is the distributable bundle.

2. **Install in Claude.** Open the Claude desktop app → Settings → Plugins → Install from file → select `garmin-coach.mcpb`.

3. **Enter your credentials.** When prompted, enter:
   - **Garmin Email** — the email address you use to log in to Garmin Connect
   - **Garmin Password** — your Garmin Connect password (stored securely in the app, never logged)

4. **Start a conversation.** The plugin activates automatically. On your first tool call, it will calibrate your personal baselines from your Garmin history — this takes around 20–30 seconds and only happens once (then silently every 7 days in the background).

## Usage Examples

**Morning check-in**
```
How am I looking this morning? Should I train or take it easy?
```
→ Pulls your overnight metrics and gives a readiness decision with reasoning.

**Training load**
```
What's my training load been like? Am I at risk of overtraining?
```
→ Shows ACWR, training status, and load trend.

**Weekly review** *(best on Sunday evening or Monday morning)*
```
Give me my weekly training review.
```
→ Compares this week to last, highlights best session, recommends next week's approach.

**Monthly review** *(best at end/start of month)*
```
How was my training this month?
```
→ Volume comparison, consistency score, VO2 Max movement, next month focus.

**Weather window**
```
When's the best time to run today?
```
→ Scores each available window and recommends the best slot with conditions.

```
What's the weather like for running in Mallusk this morning?
```
→ Checks a specific location instead of auto-detecting.

**Fitness trend**
```
Show me my VO2 Max trend over the last 6 weeks.
```

**Create a running workout**
```
Create an interval session: 10 min warmup, 6×800m at tempo pace with 90 sec recovery, 10 min cooldown.
```
→ Builds and uploads a structured workout to Garmin Connect.

**Create a strength workout**
```
Build me a leg day: squats 4×8, Romanian deadlifts 3×10, walking lunges 3×12, calf raises 4×15. Rest 90 seconds between sets.
```

**Schedule a workout**
```
Schedule that workout for this Saturday.
```
→ Pins it to your Garmin calendar.

**Force recalibration**
```
Recalibrate my baselines — my fitness has changed a lot recently.
```
→ Recomputes your HRV and RHR baselines from the latest 30 days of data.

**Set up a training plan** *(first time)*
```
Let's set up my half marathon plan.
```
→ Pulls your current Garmin race predictions (VO2 Max-based), then asks you: race name and date, any B races, training days per week, whether to include strength work, and your target time. Creates a plan file at `~/.garmin-coach-plan.json`. After this, every daily tool response includes a PLAN CONTEXT block automatically.

**Check plan status**
```
Where am I in my plan?
```
→ Shows current week and phase, race countdown, predicted vs target time, and a full phase breakdown with your current position marked.

**Start a new training cycle**
```
Clear my training plan — I've got a new race to target.
```
→ Deletes the current plan so you can run setup again for the new cycle.

## How Calibration Works

On first use, the plugin pulls your last 30 days of HRV and resting HR readings from Garmin Connect and computes your personal baselines:

- **HRV band** — your mean ± standard deviation over 30 days. This is the range considered "normal" for you.
- **RHR norm** — your 30-day average resting heart rate.

These are saved to `~/.garmin-coach.json` and refreshed silently every 7 days. This means the morning readiness assessment is always comparing your current numbers against *your* normal, not a population average — so it works regardless of whether your HRV sits at 35 or 95.

If you have fewer than 7 days of Garmin data, reasonable population-average defaults are used until more data accumulates.

## How the Training Plan Works

When `setup_training_plan` is called, it fetches your current Garmin Race Predictor values — Garmin's own VO2 Max-derived time estimates for 5k, 10k, and half marathon. If those aren't available, it falls back to a VDOT table interpolation from your VO2 Max. Either way, it labels the source so you know how much confidence to place in the number.

The plan stores: race name and date, an optional B race, training days per week, strength session count, target finish time, and predicted time at setup. It does not generate a specific week-by-week session schedule — it tracks your position in the training cycle and tells every daily tool what phase you're in.

**Phases** scale proportionally to your plan length, based on these percentage cutoffs:

| Phase | When | Focus |
|---|---|---|
| Base | First 25% | Easy aerobic only. Build the engine. |
| Development | 25–55% | One quality session/week (tempo or threshold). 80% still easy. |
| Specific | 55–80% | Race-pace intervals. This is where the time gains get banked. |
| Taper | 80–92% | Volume drops 40–50%. Keep one short quality session. Trust the process. |
| Race Week | Last 8% | Shake-out runs only. Race execution is the job now. |

The methodology throughout is **polarised 80/20** — 80% of running at easy aerobic intensity (zone 1–2), 20% at genuine quality (threshold, intervals, race pace). This is the approach with the strongest evidence base for half marathon improvement and maps cleanly onto Garmin's zone model.

## Project Structure

```
garmin-coach/
├── manifest.json        — Plugin manifest (version, tool declarations, user config)
├── server/main.py       — MCP server with all tools, Garmin API logic, calibration, weather
├── start.sh             — Self-healing startup script (rebuilds broken venvs automatically)
├── diagnose.sh          — Import checker for debugging startup issues
├── run-test.sh          — Smoke test: prompts for credentials and runs a quick data pull
├── garmin-coach.mcpb    — Distributable plugin bundle
├── backups/             — Pre-change mcpb snapshots (2 kept at all times)
└── CLAUDE.md            — Developer build/release instructions and API conventions

~/.garmin-coach.json      — Personal HRV/RHR baselines (auto-created, refreshed every 7 days)
~/.garmin-coach-plan.json — Training plan (created by setup_training_plan, deleted by clear_training_plan)
```

## Dependencies

Installed automatically by `start.sh` into a local `.venv`:

- [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) — unofficial Garmin Connect API client
- [`mcp`](https://pypi.org/project/mcp/) — Model Context Protocol server library

Weather and location use free public APIs with no key required (Open-Meteo for forecasts, ip-api.com for geolocation).

## Troubleshooting

**Plugin won't start / import errors**
Run `./diagnose.sh` from the project directory. It checks the Python environment and imports and prints the exact error.

**Calibration takes a long time on first run**
Normal — it's making ~60 API calls to Garmin (30 days × HRV + RHR). Subsequent calls are instant since results are cached.

**Morning metrics showing unexpected RED/AMBER**
Your baselines may be stale. Run `calibrate` to force a fresh 30-day recalibration.

**Weather location is wrong**
IP geolocation is city-level accurate. Pass a location name explicitly: *"check run window for Belfast"*.
