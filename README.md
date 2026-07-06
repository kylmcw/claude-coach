# Garmin Morning Coach

A Claude plugin that connects Claude to your Garmin Connect account. Pull your daily health metrics, training load, and fitness trends — and push structured workouts directly to your Garmin watch — all through natural conversation.

---

## First Time Setup

Follow these steps in order the first time you install the plugin.

### 1. Install the plugin

See [Installation](#installation) below. You'll need your Garmin Connect email and password.

### 2. Let it calibrate

On your very first tool call, the plugin pulls your last 30 days of HRV and resting HR from Garmin Connect and computes your personal baselines. This takes 20–30 seconds and only happens once (then silently refreshes every 7 days in the background). You don't need to do anything — just make any request and it runs automatically.

### 3. Set up your training plan

```
Let's set up my half marathon training plan.
```

Call `setup_training_plan` with no arguments. It fetches your current Garmin race predictions and walks you through a short questionnaire: race name and date, training days per week, whether to include strength work, blocked days, and your target time. Once created, every daily coaching response automatically includes your current phase, week number, and race countdown.

### 4. Generate your first week

```
Generate my training week.
```

After the plan is set, `generate_week` builds a full week of sessions — runs and strength — based on your phase, workload ratio, and personalized paces from your lactate threshold. It uploads each session to Garmin Connect and schedules them on your calendar.

### 5. Start your morning routine

```
Morning briefing.
```

Each morning, `get_daily_briefing` gives you everything in one call: readiness (HRV, sleep, Training Readiness score), today's scheduled session, the best weather window for running, and a single go / modify / rest verdict. When readiness is low and a quality session is planned, it proposes a swap and logs it as a coaching suggestion for your review.

---

## Features

Thirty-eight tools are available to Claude, organized by category.

**Morning & readiness**
- **get_daily_briefing** — Single-call morning briefing: readiness score, today's scheduled session, best weather window, and a go/modify/rest verdict. When AMBER or RED meets a quality session, proposes a swap automatically.
- **get_morning_metrics** — Detailed readiness breakdown: overnight HRV (with status and weekly average), resting HR, sleep score with deep/REM/light hours, body battery, stress, and Garmin's Training Readiness score — scored against your personal baselines and returned as GREEN / AMBER / RED.
- **get_recovery_trend** — 14-day day-by-day trend of HRV, resting HR, sleep score, body battery, and stress. Flags overreaching and illness patterns based on slope analysis.
- **calibrate** — Recomputes your personal HRV and resting HR baselines from the last 30 days of Garmin data. Runs automatically on first use and silently every 7 days. Call manually after a significant fitness change.

**Training load & fitness**
- **get_training_load** — Training status, 7-day and 28-day load, and acute-to-chronic workload ratio (ACWR) to flag overreaching and injury risk. Includes load focus (base / threshold / peak) and training plan context.
- **get_fitness_trend** — VO2 Max sampled weekly over the last 6 weeks so you can see whether fitness is trending up, stable, or declining.
- **get_fitness_scores** — Garmin's Endurance Score and Hill Score for today, with descriptive level labels.
- **get_recent_activities** — Your last 7 days of activities with distance, pace, duration, heart rate, and aerobic/anaerobic training effect scores.
- **analyze_run** — Deep analysis of a specific run (or your most recent if no ID given): cadence, stride length, ground contact time and balance, vertical oscillation, vertical ratio, running power, per-km splits, HR zone breakdown, and actionable form recommendations.
- **get_zones** — Personalised HR and pace zones (easy, aerobic, threshold, interval) derived from your lactate threshold using Friel running coefficients.

**Weekly & monthly reviews**
- **get_weekly_review** — This week vs last week: distance, time, sessions, ACWR, intensity distribution (methodology-aware: polarized 80/20 or pyramidal 70/20/10), recurring niggle warnings, and a specific coming-week recommendation.
- **get_monthly_review** — This month vs last month: volume, consistency, longest run, VO2 Max movement, and a focus area for next month.

**Training plan**
- **setup_training_plan** — Set up or update your race training plan. Call with no arguments to get a questionnaire driven by your Garmin race predictions. Call with `race_date` and `training_days_per_week` to create the plan.
- **get_plan_status** — Full plan breakdown: race details, countdown, current week and phase, predicted vs target time, weekly structure, and a phase-by-phase table with your current position marked.
- **generate_week** — Generate and schedule a full week of training sessions on Garmin Connect based on your current phase, ACWR, blocked days, and personalized pace/HR targets from your lactate threshold. Requires an active training plan.
- **mark_week_planned** — Mark a week as fully planned so the Monday morning coach skips workout generation for it. Called automatically by `generate_week`; call manually after setting up a custom or deload week.
- **get_week_planned** — Check whether a given week has already been marked as planned.
- **get_race_strategy** — Race-day pacing strategy: gap analysis between your goal and Garmin's prediction, per-km pace bands, half-split targets, taper checklist, and race-day cues. Accepts an optional target time override.
- **clear_training_plan** — Deletes the current plan. Requires `confirmation=true`. Use when starting a new training cycle.

**Workouts**
- **create_running_workout** — Build a structured running workout (warmup, easy, intervals, recovery, cooldown, repeats) with pace or HR zone targets and upload it to Garmin Connect.
- **create_strength_workout** — Build a structured strength workout from a library of 70+ mapped exercises and upload it to Garmin Connect.
- **schedule_workout** — Pin any uploaded workout to a specific date on your Garmin calendar so it appears on your watch that day.
- **get_scheduled_workout** — Read the workout scheduled on your Garmin calendar for a given date (defaults to tomorrow). Returns full step/exercise structure with targets.
- **get_future_schedule** — Fetch scheduled workouts for the next N days (default 7, max 90). Use before generating a week to see what's already on the calendar.
- **delete_workout** — Permanently delete a workout template from your Garmin Connect library.
- **unschedule_workout** — Remove a workout from your calendar without deleting the template.

**Strength exercises**
- **set_exercise_defaults** — Set or update default weight/sets/reps for one or more strength exercises, auto-applied when creating strength workouts.
- **get_exercise_defaults** — View current default weights, sets, and reps for all tracked strength exercises (or a single named one).
- **log_strength_progress** — Log the outcome of a strength session per exercise and get progressive-overload suggestions (+10% on success, -10% on failure), optionally applied immediately.
- **review_strength_workout** — Pull a completed strength activity from Garmin Connect and compare each exercise against stored defaults; optionally save logged weights as new defaults.
- **set_exercise_override** — Temporarily adjust the programmed weight for an exercise (percentage or kg delta) for a deload, travel, or sick week, with an optional date window that auto-reverts.
- **clear_exercise_override** — Remove active weight overrides by exercise name, label, or all at once.

**Coaching history**
- **log_workout_feedback** — Log a completed session with RPE, feel, niggles, and notes. Links to the current training cycle and builds longitudinal coaching context.
- **get_workout_history** — Retrieve logged session history with feedback, ordered by date. Used by the coach to identify fatigue patterns, load trends, and recurring niggles.
- **backfill_runs** — Scan the last N days of Garmin activities and log anything missing from history — recovers runs from days you didn't open the coach. Idempotent.
- **get_pending_suggestions** — Return all pending coaching suggestions not yet approved or denied. Suggestions are generated automatically during weekly review based on ACWR, volume trends, and session consistency.
- **approve_suggestion** — Approve or deny a pending coaching suggestion by ID. Both outcomes are recorded.

**Weather**
- **get_run_window** — Checks today's weather and recommends the best time to run. On weekdays evaluates morning (6–9am) and lunch (12–1pm) slots. On weekends evaluates all daylight windows. Auto-detects your location, or accepts a named place (e.g. "Belfast").

---

## Prerequisites

- **Python 3.10+** (3.14 preferred)
- **A Garmin Connect account** with email/password login enabled
- **Claude desktop app** with plugin support

---

## Installation

1. **Get the plugin file.** You need the `garmin-coach.mcpb` file — this is the distributable bundle.

2. **Install in Claude.** Open the Claude desktop app → Settings → Plugins → Install from file → select `garmin-coach.mcpb`.

3. **Enter your credentials.** When prompted, enter:
   - **Garmin Email** — the email address you use to log in to Garmin Connect
   - **Garmin Password** — your Garmin Connect password (stored securely in the app, never logged)

4. **Follow the [First Time Setup](#first-time-setup) steps above.**

---

## Usage Examples

**Morning briefing**
```
Morning briefing.
```
→ Readiness score, today's session, best weather window, and a single verdict.

**Detailed readiness**
```
How am I looking this morning? Should I train or take it easy?
```
→ Full overnight metrics with GREEN / AMBER / RED decision and reasoning.

**Recovery trend**
```
How has my recovery been looking over the last two weeks?
```
→ Day-by-day HRV, sleep, and body battery trend with overreaching flags.

**Training load**
```
What's my training load been like? Am I at risk of overtraining?
```
→ ACWR, training status, load focus, and trend.

**Weekly review** *(best on Sunday evening or Monday morning)*
```
Give me my weekly training review.
```
→ This week vs last, intensity distribution, niggle scan, next-week recommendation.

**Monthly review** *(best at end/start of month)*
```
How was my training this month?
```
→ Volume, consistency, VO2 Max movement, next-month focus.

**Race strategy**
```
Give me my race-day strategy for Larne.
```
→ Per-km pace bands, half-split targets, taper checklist, race-day cues.

**Generate a training week**
```
Generate my training week starting Monday.
```
→ Builds and schedules a full week of sessions based on your phase and current load.

**Check the calendar**
```
What's on my schedule this week?
```
→ Day-by-day view of what's already on your Garmin calendar.

**Weather window**
```
When's the best time to run today?
```
→ Scores each available window and recommends the best slot with conditions.

**Analyze a run**
```
Analyze my run from yesterday.
```
→ Form metrics, HR zone breakdown, per-km splits, and coaching recommendations.

**Create a running workout**
```
Create an interval session: 10 min warmup, 6×800m at 5k pace with 90 sec recovery, 10 min cooldown.
```
→ Builds and uploads a structured workout to Garmin Connect.

**Create a strength workout**
```
Build me a leg day: squats 4×8, Romanian deadlifts 3×10, walking lunges 3×12, calf raises 4×15. Rest 90 seconds between sets.
```

**Log a session**
```
Log today's tempo run — RPE 7, legs felt heavy, slight tightness in right calf.
```
→ Saves feedback to coaching history and links to the current training cycle.

**Force recalibration**
```
Recalibrate my baselines — my fitness has changed a lot recently.
```
→ Recomputes your HRV and RHR baselines from the latest 30 days of data.

---

## How Calibration Works

On first use, the plugin pulls your last 30 days of HRV and resting HR readings from Garmin Connect and computes your personal baselines:

- **HRV band** — your mean ± standard deviation over 30 days. This is the range considered "normal" for you.
- **RHR norm** — your 30-day average resting heart rate.

These are saved to `~/.garmin-coach.json` and refreshed silently every 7 days. Morning readiness is always compared against *your* normal, not a population average — so it works regardless of whether your HRV sits at 35 or 95.

If you have fewer than 7 days of Garmin data, population-average defaults are used until more data accumulates.

---

## How the Training Plan Works

When `setup_training_plan` is called, it fetches your Garmin Race Predictor values — VO2 Max-derived time estimates for 5k, 10k, and half marathon. If those aren't available, it falls back to VDOT table interpolation from your VO2 Max. Either way, it labels the source so you know the confidence level.

The plan stores: race name and date, an optional B race, training days per week, strength session count, blocked days, training methodology, target time, and predicted time at setup. It tracks your position in the training cycle and injects phase context into every daily tool response.

**Phases** scale proportionally to your plan length:

| Phase | When | Focus |
|---|---|---|
| Base | First 25% | Easy aerobic only. Build the engine. |
| Development | 25–55% | One quality session/week (tempo or threshold). |
| Specific | 55–80% | Race-pace intervals. This is where time gains are banked. |
| Taper | 80–92% | Volume drops 40–50%. Keep one short quality session. |
| Race Week | Last 8% | Shake-out runs only. Race execution is the job now. |

**Training methodologies** — chosen when setting up the plan:

- **Polarized (80/20)** — 80% of running at easy aerobic intensity (Z1–2), 20% at genuine quality (threshold, intervals, race pace). Strongest evidence base for half marathon improvement.
- **Pyramidal** — ~70% easy (Z1–2), ~20% moderate/tempo (Z3), ~10% hard (Z4–5). More tempo volume; suits higher-mileage athletes who benefit from a larger aerobic middle zone.

Both methodologies are supported in `generate_week`, weekly review intensity distribution, and all coaching feedback.

---

## Project Structure

```
garmin-coach/
├── manifest.json              — Plugin manifest (version, 38 tool declarations, user config)
├── server/
│   ├── main.py                — MCP server entry point and if/elif tool dispatcher
│   ├── tools.py               — 38 Tool schema definitions
│   ├── utils.py                — Shared helpers
│   ├── garmin/                 — Garmin API layer
│   │   ├── client.py           — Lazy-init Garmin client singleton
│   │   ├── calibration.py      — HRV/RHR baseline calibration and storage
│   │   ├── readiness.py        — Morning metrics fetch and GREEN/AMBER/RED assessment
│   │   ├── analysis.py         — Run dynamics analysis and form coaching
│   │   ├── training.py         — Training load, activities, weekly/monthly reviews
│   │   └── schedule.py         — Garmin Connect calendar fetch and formatting
│   ├── coaching/                — Coaching logic
│   │   ├── plan.py             — Training plan CRUD, phase logic, session planning
│   │   ├── thresholds.py        — Lactate threshold zones from Garmin data
│   │   ├── briefing.py          — Daily briefing (readiness + schedule + weather + verdict)
│   │   ├── race_strategy.py    — Race-day pacing strategy and taper checklist
│   │   ├── recovery_trend.py   — 14-day recovery trend with overreaching detection
│   │   └── weather.py          — Weather windows, location resolution, scoring
│   ├── db/                     — SQLite persistence
│   │   ├── history.py          — Workouts/feedback/coach_log + backfill/auto-log
│   │   └── exercises.py        — Exercise registry, aliases, defaults, progression
│   └── workouts/                — Workout creation
│       ├── steps.py            — Running/strength step builders
│       ├── workouts.py         — Uploaders to Garmin Connect
│       └── validate.py         — PreToolUse guard against the training plan
├── start.sh                   — Self-healing startup script (rebuilds broken venvs)
├── run-test.sh                — Smoke test: prompts for credentials and runs a quick data pull
├── deploy.sh                  — Rsyncs the project into Kyle's and Kayleigh's local Claude extensions and restarts the app
├── garmin-coach.mcpb          — Distributable plugin bundle
├── garmin-coach-kayleigh.mcpb — Same code, patched manifest name/display_name
├── backups/                   — Pre-change mcpb snapshots (2 kept at all times)
├── tests/                     — Pytest unit-test suite (no network/creds required)
└── CLAUDE.md                  — Developer build/release instructions and API conventions

~/.garmin-coach.json           — Personal HRV/RHR baselines (auto-created, refreshed every 7 days)
~/.garmin-coach-plan.json      — Training plan (created by setup_training_plan)
~/.garmin-coach-history.db     — SQLite workout history and coaching suggestions
```

---

## Dependencies

Installed automatically by `start.sh` into a local `.venv`:

- [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) — unofficial Garmin Connect API client
- [`mcp`](https://pypi.org/project/mcp/) — Model Context Protocol server library

Weather and location use free public APIs with no key required (Open-Meteo for forecasts, ip-api.com for geolocation).

---

## Troubleshooting

**Plugin won't start / import errors**
Run `.venv/bin/python3 -c "import sys; sys.path.insert(0, 'server'); import main; print('OK')"` from the project directory. It checks the Python environment and imports and prints the exact error.

**Calibration takes a long time on first run**
Normal — it's making ~60 API calls to Garmin (30 days × HRV + RHR). Subsequent calls are instant since results are cached.

**Morning metrics showing unexpected RED/AMBER**
Your baselines may be stale. Run `calibrate` to force a fresh 30-day recalibration.

**Weather location is wrong**
IP geolocation is city-level accurate. Pass a location name explicitly: *"check run window for Belfast"*.
