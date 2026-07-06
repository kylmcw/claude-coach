# Garmin Morning Coach — Changelog

## 1.23.3 (2026-07-06) — Docs audit + missing manifest tool

### Fixed
- `manifest.json` was missing the `backfill_runs` tool declaration even though it's fully implemented (`db/history.py`, dispatched in `main.py`, schema in `tools.py`) — the built `.mcpb` never exposed it to Claude. Added.

### Docs
- README's Project Structure section still showed the pre-1.18.0 flat `server/*.py` layout; updated to the current `server/{garmin,coaching,db,workouts}/` subpackage layout.
- README's tool count and Features list were stuck at 28; updated to the current 38 (added `get_zones`, `mark_week_planned`, `get_week_planned`, `set_exercise_defaults`, `get_exercise_defaults`, `set_exercise_override`, `clear_exercise_override`, `log_strength_progress`, `review_strength_workout`, `backfill_runs`).
- Removed dead `./diagnose.sh` references from README and root `CLAUDE.md` — the script was deleted in 1.23.2; both now point at the inline import smoke-test.
- `.claude/skills/release.md` only described the old single-bundle release flow; updated to match `CLAUDE.md`'s dual-bundle (Kyle + Kayleigh) build and `deploy.sh` step.

## 1.23.2 (2026-07-05) — Native training-status load engine + weekly-context awareness

Drive load/volume decisions off Garmin's native training status + load focus instead of the ACWR ratio, give single-run analysis whole-week context, and factor logged RPE/feel/niggles into reviews and week planning.

### Load engine
- `classify_load_state()` maps `trainingStatusFeedbackPhrase` + `trainingBalanceFeedbackPhrase` onto cutback/hold/add/unknown tiers (keyword-matched, robust to firmware phrase variants). Drives `generate_week_suggestions`, `_week_recommendation`, `assess_training_state` (renamed from `assess_acwr`), and `plan_week_sessions` volume scaling.
- `fetch_training_load()` now digs the real nested metrics-service shape; uses `dailyAcuteChronicWorkloadRatio` (not `acwrPercent`) for the displayed ACWR.

### Weekly context + feedback
- `analyze_run` pulls last-7-day activities + load; `build_week_context` surfaces "this week so far" including quality already done, so it stops prescribing intensity that's already been run.
- `assess_recent_feedback()` (`db/history`) scans logged RPE/feel/niggles; weekly and monthly reviews now print a feedback block; `plan_week_sessions` backs off volume on recurring niggle / high-RPE trend even when Garmin load looks fine. Replaces `_scan_niggle_patterns` with the unified scanner.

### ACWR reference-only guardrails
- Every ACWR surface now annotated "reference only — training status governs" so the coaching model stops re-deriving the old 0.8–1.3 danger-band rubric. `running-coach` agent persona updated to match.

### Fixed
- **Distance laps were being created as "lap press" steps.** `build_running_steps()` set the distance end condition's `conditionTypeId` from `ConditionType.DISTANCE`, but the `garminconnect` library defines that as `1` — Garmin's id for lap.button. Every distance-based lap was silently stored as a "press lap button" step instead of a distance lap. Added explicit `COND_TIME`/`COND_DISTANCE` constants with Garmin's real values (2 and 3) and use those instead of the library enum. Time laps were already correct and unaffected.
- Removed dead `diagnose.sh` (superseded by the inline import smoke-test — see 1.23.3 docs fix above for catching up the references to it).

Tests: +30 (`classify_load_state`, `assess_recent_feedback`, `build_week_context`, `plan_week_sessions` feedback back-off). Full suite 257 passing.

## 1.18.2 (2026-06-11) — Bugs caught by the new unit-test suite

Added an isolated unit-test suite (`tests/`, pytest, 228 tests, no network/creds) across all domain folders. Writing it surfaced three production bugs, now fixed:

### Fixed
- **Planned-workout reconciliation was silently broken.** `_planned_row_for_date` built its result dict from `[d[0] for d in PRAGMA table_info(...)]`, but `d[0]` is the column index (`cid`), not the name — so with `row_factory=sqlite3.Row` (used by auto-log and backfill) it returned an integer-keyed dict and `planned["id"]` raised `KeyError`. Planned runs were never marked complete (the error was swallowed). Now indexes the row against real column names.
- **Strength progression over-jumped at plate boundaries.** `_round_weight` ceilinged on a float-error value (`100 * 1.1 == 110.00000000000001`), bumping a clean +10% to an extra plate (110 → 112.5). Rounds to 6dp before snapping to 2.5 kg.
- **Workout guard could silently disable itself.** The `validate.py` PreToolUse hook still used the pre-centralization profile suffix and could resolve `PLAN_FILE` to a literal `${...}` path → `load_plan()` returns None → validation skipped. Applied the same blank/placeholder guard inline.

## 1.18.1 (2026-06-10) — Backfill review fixes

### Fixed
- `log_workout_to_history` no longer creates orphan feedback rows (`workout_id=0`) when an activity collides with an existing `(date, type)` row. It now detects the ignored `INSERT OR IGNORE` via `cursor.rowcount`, resolves the existing row id, and only writes feedback against a real row. Surfaced by `backfill_runs` (e.g. two runs the same day).
- `fetch_recent_activities(days)` now pages through the activity feed until it passes the cutoff, so long backfill windows (up to 90 days) aren't truncated by a single fixed-size fetch. Short windows still resolve in one API call.
- `backfill_runs` reconciles each activity under its own try/except, so one malformed activity no longer aborts the whole backfill.

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
