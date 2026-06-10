# Garmin Morning Coach — Changelog

## 1.18.0 (2026-06-10) — Run backfill, profile-file fix, LTHR cleanup

### New: `backfill_runs`
The morning auto-log only ever reconciles *yesterday*, and only when you run the morning check — so any run on a day you didn't open the coach was lost to the history DB, with no way to recover it. New **`backfill_runs`** tool scans the last N days of Garmin activities (default 14, max 90) and logs anything not already recorded. Idempotent — already-logged activities are skipped (deduped on `activity_id`). The per-activity reconcile logic is now shared between auto-log and backfill (`_reconcile_activity`), and `fetch_recent_activities` takes a `days` window.

### Fixed
- **Stray profile files.** The optional `profile_name` user_config, when left blank, could pass the unexpanded literal `${user_config.profile_name}` as `GARMIN_COACH_PROFILE`, producing stray `~/.garmin-coach-${user_config.profile_name}*` files that silently split history/baselines. Profile-suffix logic is now centralized in `utils.get_profile_suffix()` (treats empty *or* an unexpanded `${...}` placeholder as the default profile) and the two stray files were removed.
- Removed dead code left over from the LTHR (lactate threshold) zone refactor: the unused `get_zones`/`fmt_pace` import in `main.py` and the unused `lt_sec` variable in `plan.py`.
- Corrected the misleading "7 days" comment on the readiness LT-zone cache — it is a per-day cache (keyed on the current date).
- Repaired broken hook paths in `.claude/`: the PreToolUse workout validator and PostToolUse review hooks pointed at the old `garmin-coach` project directory and silently failed after the project was renamed to `claude-coach`. They now resolve via `$CLAUDE_PROJECT_DIR` (with a file-relative fallback) so a future rename can't break them.

## 1.11.2 (2026-05-23) — Coach guardrails, training history, auto-logging

### New: Training history database
All workouts are now persisted to a local SQLite database (`~/.garmin-coach-history.db`), scoped by training cycle. History spans races — so when you start your next block, the coach has context going back as far as you've been using it.

Two new tools:
- **`log_workout_feedback`** — log a completed session with RPE, subjective feel, niggles, and notes. Call this after any run or gym session.
- **`get_workout_history`** — retrieve logged history with feedback. The running-coach agent uses this automatically when making decisions.

### New: Auto-logging of missed and completed workouts
`get_morning_metrics` now silently compares yesterday's Garmin calendar against actual recorded activities. If a workout was scheduled but nothing was done, it's automatically logged as missed. If an activity was recorded, it's logged as completed with distance/pace/HR pulled from Garmin. No manual input needed — just run your morning check as normal.

### New: Workout guardrails (PreToolUse hook)
A validation hook now fires before any running workout is created. It checks the proposed workout against your current training plan phase and week:
- **Distance cap** — hard ceiling per phase (Base: 14 km, Development: 18 km, Specific: 21 km, Taper: 12 km, Race Week: 6 km), plus an early-base ceiling of 8 km for weeks 1–3.
- **Intensity gate** — interval and tempo steps are blocked in Base and Race Week phases. Works recursively, so intervals nested inside repeat blocks are caught correctly.

If a workout violates the plan, the tool call is blocked and you get a clear explanation before anything is sent to Garmin.

### New: Agent definitions
Three specialist agents are now configured under `.claude/agents/`:
- **`running-coach`** — master-level coach with access to all Garmin tools and your calibration baselines. Grounds every decision in data, not vibes.
- **`senior-engineer`** — handles implementation work on the MCP server.
- **`post-edit-reviewer`** — runs automatically after every code edit. Performs functional analysis, static analysis, code smell detection, error handling review, and security checks. Returns a prioritised P1/P2/P3 fix list.

---

## 1.10.x and earlier
Initial release. HRV/RHR calibration, morning readiness scoring (GREEN/AMBER/RED), training load and ACWR, VO2 Max trend, running dynamics analysis, weather windows, workout creation/scheduling, and half-marathon training plan management.
