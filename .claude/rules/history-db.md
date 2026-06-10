---
paths:
  - "server/db/history.py"
  - "server/db/exercises.py"
---

# History DB Rules

## Tables

- `training_cycles` — one row per race/plan cycle
- `workouts` — planned and completed sessions, linked to cycle; `(date, type)` unique index
- `feedback` — RPE/feel/niggles/notes, linked to workout row
- `coach_log` — coaching suggestions; `approved IS NULL` = pending, 1 = accepted, 0 = denied
- `exercise_defaults` — default weight/sets/reps per exercise variation; `name` is unique key
- `garmin_exercises` — canonical Garmin exercise registry; `(category, exercise_name)` unique
- `exercise_aliases` — many aliases → one `garmin_exercises` row (FK); `alias` is unique

## Migration Pattern

New columns are added with `ALTER TABLE ... ADD COLUMN` wrapped in try/except `sqlite3.OperationalError` — safe to run repeatedly on existing DBs. Add new columns to the migration loop in `init_history_db`, not to the CREATE TABLE script (so existing users get them without a full DB drop).

## Exercise Registry Seeding

`_seed_garmin_exercises(conn)` runs inside `init_history_db` and is **idempotent** — it checks `SELECT COUNT(*) FROM garmin_exercises` first and returns immediately if > 0. Only seeds on a fresh DB.

`_SEED_MAP` at module top is the source of truth for the seed data. After seeding, the DB is the source of truth — editing `_SEED_MAP` has no effect on existing installs.

## `set_exercise_defaults` Caller Contract

`garmin_category` and `garmin_exercise_name` must be pre-resolved by the caller (`main.py`) before calling `set_exercise_defaults`. `db/exercises.py` does not import from `workouts/` (circular import risk). Resolution uses `lookup_garmin_exercise(name)` from `db/exercises.py` which queries the DB.

## Connection Pattern

Every public function opens its own `sqlite3.connect(HISTORY_DB)` and closes in a `finally` block. No long-lived connections. `conn.row_factory = sqlite3.Row` when returning dicts.
