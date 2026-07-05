import sqlite3
import sys
from datetime import date, timedelta

from db import HISTORY_DB
from db.exercises import _create_exercise_tables
from coaching.plan import get_plan_phase, load_plan
from garmin.training import fetch_recent_activities


def init_history_db() -> None:
    """Create the history DB and tables if they don't exist. Safe to call on every startup."""
    conn = sqlite3.connect(HISTORY_DB)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS training_cycles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                race_name     TEXT NOT NULL,
                race_date     TEXT NOT NULL,
                race_distance TEXT,
                target_time   TEXT,
                created_on    TEXT NOT NULL,
                completed_on  TEXT,
                notes         TEXT
            );
            CREATE TABLE IF NOT EXISTS workouts (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id                    INTEGER REFERENCES training_cycles(id),
                date                        TEXT NOT NULL,
                activity_id                 INTEGER,
                garmin_workout_id           INTEGER,
                garmin_scheduled_id         INTEGER,
                workout_name                TEXT,
                type                        TEXT,
                phase                       TEXT,
                week_number                 INTEGER,
                planned_distance_km         REAL,
                actual_distance_km          REAL,
                avg_pace                    TEXT,
                avg_hr                      INTEGER,
                completed                   INTEGER DEFAULT 1,
                avg_cadence                 REAL,
                ground_contact_time_ms      REAL,
                ground_contact_balance_left REAL,
                vertical_oscillation_cm     REAL,
                vertical_ratio_pct          REAL,
                aerobic_te                  REAL,
                anaerobic_te                REAL,
                activity_training_load      REAL,
                elevation_gain_m            REAL,
                avg_respiration_rate        REAL,
                created_at                  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id  INTEGER REFERENCES workouts(id),
                date        TEXT NOT NULL,
                rpe         INTEGER,
                feel        TEXT,
                niggles     TEXT,
                notes       TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coach_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id         INTEGER REFERENCES training_cycles(id),
                date             TEXT NOT NULL,
                trigger          TEXT,
                context_summary  TEXT,
                recommendation   TEXT,
                rationale        TEXT,
                action_type      TEXT,
                approved         INTEGER,
                notes            TEXT,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_workout_date_type ON workouts(date, type);
            CREATE TABLE IF NOT EXISTS week_plans (
                week_start  TEXT PRIMARY KEY,
                planned_at  TEXT NOT NULL,
                planned_by  TEXT
            );
        """)
        conn.commit()

        _create_exercise_tables(conn)

        # Migrate existing DBs: add new columns if not present
        new_cols_workouts = [
            ("avg_cadence",                 "REAL"),
            ("ground_contact_time_ms",      "REAL"),
            ("ground_contact_balance_left", "REAL"),
            ("vertical_oscillation_cm",     "REAL"),
            ("vertical_ratio_pct",          "REAL"),
            ("aerobic_te",                  "REAL"),
            ("anaerobic_te",                "REAL"),
            ("activity_training_load",      "REAL"),
            ("elevation_gain_m",            "REAL"),
            ("avg_respiration_rate",        "REAL"),
            ("garmin_scheduled_id",         "INTEGER"),
            ("workout_name",                "TEXT"),
        ]
        for col, typ in new_cols_workouts:
            try:
                conn.execute(f"ALTER TABLE workouts ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        new_cols_coach_log = [
            ("action_type", "TEXT"),
            ("notes",       "TEXT"),
        ]
        for col, typ in new_cols_coach_log:
            try:
                conn.execute(f"ALTER TABLE coach_log ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()


def mark_week_planned(week_start: str, planned_by: str = "generate_week") -> None:
    """Record that a week has been fully planned. week_start = ISO date of Monday."""
    from datetime import datetime
    conn = sqlite3.connect(HISTORY_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO week_plans (week_start, planned_at, planned_by) VALUES (?, ?, ?)",
            (week_start, datetime.utcnow().isoformat(), planned_by),
        )
        conn.commit()
    finally:
        conn.close()


def is_week_planned(week_start: str) -> dict:
    """Return planning status for a week. week_start = ISO date of Monday."""
    conn = sqlite3.connect(HISTORY_DB)
    try:
        row = conn.execute(
            "SELECT week_start, planned_at, planned_by FROM week_plans WHERE week_start = ?",
            (week_start,),
        ).fetchone()
        if row:
            return {"planned": True, "week_start": row[0], "planned_at": row[1], "planned_by": row[2]}
        return {"planned": False, "week_start": week_start}
    finally:
        conn.close()


def _get_or_create_cycle(conn: sqlite3.Connection, plan: dict) -> int:
    """Return the training_cycles.id for the current plan, inserting a row if needed."""
    race_date = plan["race_date"]
    row = conn.execute(
        "SELECT id FROM training_cycles WHERE race_date = ?", (race_date,)
    ).fetchone()
    if row:
        return row[0]
    conn.execute(
        """INSERT INTO training_cycles
               (race_name, race_date, race_distance, target_time, created_on)
           VALUES (?, ?, ?, ?, ?)""",
        (
            plan.get("race_name", "Unknown"),
            race_date,
            plan.get("race_distance"),
            plan.get("target_time"),
            plan.get("created_on", date.today().isoformat()),
        ),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def log_workout_to_history(
    date_str: str,
    activity_id: int | None,
    garmin_workout_id: int | None,
    workout_type: str,
    planned_distance_km: float | None,
    actual_distance_km: float | None,
    avg_pace: str | None,
    avg_hr: int | None,
    completed: bool,
    rpe: int | None,
    feel: str | None,
    niggles: str | None,
    notes: str | None,
    *,
    avg_cadence: float | None = None,
    ground_contact_time_ms: float | None = None,
    ground_contact_balance_left: float | None = None,
    vertical_oscillation_cm: float | None = None,
    vertical_ratio_pct: float | None = None,
    aerobic_te: float | None = None,
    anaerobic_te: float | None = None,
    activity_training_load: float | None = None,
    elevation_gain_m: float | None = None,
    avg_respiration_rate: float | None = None,
) -> int:
    """
    Persist a workout + optional feedback to the history DB.
    Auto-links to the current training cycle if a plan exists.
    Returns the new workouts.id.
    """
    plan = load_plan()
    conn = sqlite3.connect(HISTORY_DB)
    try:
        cycle_id    = None
        phase       = None
        week_number = None

        if plan:
            cycle_id      = _get_or_create_cycle(conn, plan)
            created_on    = date.fromisoformat(plan["created_on"])
            target_date   = date.fromisoformat(date_str)
            weeks_elapsed = (target_date - created_on).days // 7
            week_number   = min(weeks_elapsed + 1, plan["total_weeks"])
            phase         = get_plan_phase(week_number, plan["total_weeks"])

        cur = conn.execute(
            """INSERT OR IGNORE INTO workouts
                   (cycle_id, date, activity_id, garmin_workout_id, type, phase,
                    week_number, planned_distance_km, actual_distance_km,
                    avg_pace, avg_hr, completed,
                    avg_cadence, ground_contact_time_ms, ground_contact_balance_left,
                    vertical_oscillation_cm, vertical_ratio_pct,
                    aerobic_te, anaerobic_te, activity_training_load,
                    elevation_gain_m, avg_respiration_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cycle_id, date_str, activity_id, garmin_workout_id, workout_type, phase,
             week_number, planned_distance_km, actual_distance_km,
             avg_pace, avg_hr, int(completed),
             avg_cadence, ground_contact_time_ms, ground_contact_balance_left,
             vertical_oscillation_cm, vertical_ratio_pct,
             aerobic_te, anaerobic_te, activity_training_load,
             elevation_gain_m, avg_respiration_rate),
        )
        conn.commit()

        if cur.rowcount:
            workout_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            # (date, type) already existed — INSERT OR IGNORE was a no-op. Resolve the
            # existing row so feedback never attaches to a phantom workout_id=0.
            row = conn.execute(
                "SELECT id FROM workouts WHERE date = ? AND type = ?",
                (date_str, workout_type),
            ).fetchone()
            workout_row_id = row[0] if row else 0

        if workout_row_id and any(v is not None for v in [rpe, feel, niggles, notes]):
            conn.execute(
                """INSERT INTO feedback (workout_id, date, rpe, feel, niggles, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (workout_row_id, date_str, rpe, feel, niggles, notes),
            )
            conn.commit()

        return workout_row_id
    finally:
        conn.close()


def fetch_workout_history(limit: int = 20, current_cycle_only: bool = True) -> list[dict]:
    """
    Return workouts + feedback ordered by date DESC.
    If current_cycle_only and a plan exists, scope to that race's cycle.
    """
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    try:
        cycle_filter = ""
        params: list  = [limit]

        if current_cycle_only:
            plan = load_plan()
            if plan:
                row = conn.execute(
                    "SELECT id FROM training_cycles WHERE race_date = ?",
                    (plan["race_date"],),
                ).fetchone()
                if row:
                    cycle_filter = "WHERE w.cycle_id = ? "
                    params       = [row[0], limit]
                else:
                    return []

        rows = conn.execute(
            f"""SELECT w.date, w.type, w.phase, w.week_number,
                       w.planned_distance_km, w.actual_distance_km,
                       w.avg_pace, w.avg_hr, w.completed,
                       f.rpe, f.feel, f.niggles, f.notes AS feedback_notes,
                       tc.race_name, tc.race_date
                FROM workouts w
                LEFT JOIN feedback  f  ON f.workout_id = w.id
                LEFT JOIN training_cycles tc ON tc.id  = w.cycle_id
                {cycle_filter}
                ORDER BY w.date DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


_NIGGLE_KEYWORDS = (
    "calf", "achilles", "knee", "hip", "hamstring", "shin", "ankle", "foot",
    "plantar", "it band", "quad", "glute", "back", "shoulder", "arm",
)
_ROUGH_FEELINGS = ("bad", "rough", "flat", "tired", "poor", "sluggish", "awful")


def assess_recent_feedback(days: int = 14, limit: int = 60) -> dict:
    """
    Scan recent logged feedback (RPE / feel / niggles) for warning patterns.
    Reads the workouts⨝feedback join via fetch_workout_history — no new schema.

    Returns:
      niggle_warnings — list[str], one per body part mentioned >=3x in window
      avg_rpe         — mean logged RPE in window (float) or None
      high_rpe_count  — sessions with RPE >= 8
      rough_feel_count— sessions whose feel reads rough/flat/tired
      backoff         — bool: recent feedback warrants easing next week
      reason          — str | None: why backoff was set
      lines           — list[str]: ready-to-print review lines (niggles + RPE/feel)
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = fetch_workout_history(limit=limit, current_cycle_only=False)
    except Exception:
        rows = []
    rows = [r for r in rows if (r.get("date") or "") >= cutoff]

    niggle_counts: dict[str, int] = {}
    rpes: list[int] = []
    high_rpe_count = 0
    rough_feel_count = 0

    for r in rows:
        niggle = (r.get("niggles") or "").lower()
        for kw in _NIGGLE_KEYWORDS:
            if kw in niggle:
                niggle_counts[kw] = niggle_counts.get(kw, 0) + 1
        rpe = r.get("rpe")
        if isinstance(rpe, (int, float)):
            rpes.append(int(rpe))
            if rpe >= 8:
                high_rpe_count += 1
        feel = (r.get("feel") or "").lower()
        if feel and any(f in feel for f in _ROUGH_FEELINGS):
            rough_feel_count += 1

    niggle_warnings = [
        f"RECURRING NIGGLE: '{kw}' mentioned {n}x in the last {days} days — "
        "monitor closely and consider reducing load or seeking assessment."
        for kw, n in niggle_counts.items() if n >= 3
    ]
    avg_rpe = round(sum(rpes) / len(rpes), 1) if rpes else None

    # Back off if a niggle keeps recurring, or effort/feel trend shows fatigue.
    reason = None
    if niggle_warnings:
        reason = niggle_warnings[0].split(" — ")[0]
    elif high_rpe_count >= 3:
        reason = f"{high_rpe_count} sessions at RPE >=8 in the last {days} days"
    elif avg_rpe is not None and avg_rpe >= 7.5:
        reason = f"average logged RPE {avg_rpe} over {len(rpes)} sessions"
    elif rough_feel_count >= 3:
        reason = f"{rough_feel_count} sessions logged as rough/flat in the last {days} days"
    backoff = reason is not None

    lines = list(niggle_warnings)
    if avg_rpe is not None:
        rpe_line = f"RPE (last {days}d): avg {avg_rpe} over {len(rpes)} session(s)"
        if high_rpe_count:
            rpe_line += f" — {high_rpe_count} at RPE >=8 (hard)"
        lines.append(rpe_line)
    if rough_feel_count >= 2:
        lines.append(f"FEEL: {rough_feel_count} recent session(s) logged rough/flat — watch cumulative fatigue.")

    return {
        "niggle_warnings":  niggle_warnings,
        "avg_rpe":          avg_rpe,
        "high_rpe_count":   high_rpe_count,
        "rough_feel_count": rough_feel_count,
        "backoff":          backoff,
        "reason":           reason,
        "lines":            lines,
    }


def _planned_row_for_date(conn: sqlite3.Connection, date_str: str, sport: str | None = None) -> dict | None:
    """Return the first incomplete planned row for date_str, optionally filtered by sport."""
    if sport and "running" in sport:
        row = conn.execute(
            """SELECT * FROM workouts WHERE date = ? AND completed = 0
               AND type IN ('easy_run','long_run','tempo','intervals')
               ORDER BY id LIMIT 1""",
            (date_str,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM workouts WHERE date = ? AND completed = 0 ORDER BY id LIMIT 1",
            (date_str,),
        ).fetchone()
    if row is None:
        return None
    # PRAGMA table_info columns are (cid, name, ...) — name is index 1, not 0.
    # Index row positionally so this works whether or not row_factory is sqlite3.Row.
    cols = [c[1] for c in conn.execute("PRAGMA table_info(workouts)").fetchall()]
    return {col: row[i] for i, col in enumerate(cols)}


def _reconcile_activity(conn: sqlite3.Connection, act: dict) -> str | None:
    """
    Log a single Garmin activity into the DB for its own date.
    If an incomplete planned row exists for that date, UPDATE it to completed with the
    actual data; otherwise INSERT a new completed row (deduped on activity_id).
    Returns a human-readable log string, or None if nothing was written (already logged).
    """
    date_str    = act["date"]
    sport       = act.get("type", "unknown")
    wtype = (
        "easy_run" if "running" in sport else
        "strength" if "strength" in sport else
        sport
    )
    actual_km   = act["distance_km"] or None
    avg_pace    = act["avg_pace"] if act["avg_pace"] != "N/A" else None
    avg_hr      = act["avg_hr"]
    activity_id = act.get("activity_id")

    planned = _planned_row_for_date(conn, date_str, sport)

    if planned:
        conn.execute(
            """UPDATE workouts SET
                   completed = 1,
                   activity_id = ?,
                   actual_distance_km = ?,
                   avg_pace = ?,
                   avg_hr = ?,
                   avg_cadence = ?,
                   ground_contact_time_ms = ?,
                   ground_contact_balance_left = ?,
                   vertical_oscillation_cm = ?,
                   vertical_ratio_pct = ?,
                   aerobic_te = ?,
                   anaerobic_te = ?,
                   activity_training_load = ?,
                   elevation_gain_m = ?,
                   avg_respiration_rate = ?
               WHERE id = ?""",
            (
                activity_id, actual_km, avg_pace, avg_hr,
                act.get("avg_cadence"),
                act.get("ground_contact_time_ms"),
                act.get("ground_contact_balance_left"),
                act.get("vertical_oscillation_cm"),
                act.get("vertical_ratio_pct"),
                act.get("aerobic_te"),
                act.get("anaerobic_te"),
                act.get("activity_training_load"),
                act.get("elevation_gain_m"),
                act.get("avg_respiration_rate"),
                planned["id"],
            ),
        )
        conn.commit()
        return f"Logged completed workout: {act['name']} ({date_str})"

    already = conn.execute(
        "SELECT COUNT(*) FROM workouts WHERE date = ? AND activity_id = ?",
        (date_str, activity_id),
    ).fetchone()[0]
    if already:
        return None

    log_workout_to_history(
        date_str=date_str,
        activity_id=activity_id,
        garmin_workout_id=None,
        workout_type=wtype,
        planned_distance_km=None,
        actual_distance_km=actual_km,
        avg_pace=avg_pace,
        avg_hr=avg_hr,
        completed=True,
        rpe=None,
        feel=None,
        niggles=None,
        notes="auto-logged from Garmin activity",
        avg_cadence=act.get("avg_cadence"),
        ground_contact_time_ms=act.get("ground_contact_time_ms"),
        ground_contact_balance_left=act.get("ground_contact_balance_left"),
        vertical_oscillation_cm=act.get("vertical_oscillation_cm"),
        vertical_ratio_pct=act.get("vertical_ratio_pct"),
        aerobic_te=act.get("aerobic_te"),
        anaerobic_te=act.get("anaerobic_te"),
        activity_training_load=act.get("activity_training_load"),
        elevation_gain_m=act.get("elevation_gain_m"),
        avg_respiration_rate=act.get("avg_respiration_rate"),
    )
    return f"Logged completed workout: {act['name']} ({date_str})"


def auto_log_missed_workouts() -> list[str]:
    """
    Compare yesterday's activities against planned rows in the DB and reconcile them.
    Returns a list of human-readable strings describing what was logged.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    logged: list[str] = []

    try:
        acts_yesterday = [a for a in fetch_recent_activities() if a["date"] == yesterday]
        if not acts_yesterday:
            return []

        conn = sqlite3.connect(HISTORY_DB)
        conn.row_factory = sqlite3.Row
        try:
            for act in acts_yesterday:
                msg = _reconcile_activity(conn, act)
                if msg:
                    logged.append(msg)
        finally:
            conn.close()

    except Exception:
        pass  # never crash morning metrics over a logging failure

    return logged


def backfill_runs(days: int = 14) -> list[str]:
    """
    Reconcile the last `days` days of Garmin activities into the DB.
    Unlike auto_log_missed_workouts (yesterday only), this walks the whole window so
    runs that were never captured on their morning check get logged retroactively.
    Idempotent — already-logged activities are skipped (deduped on activity_id).
    """
    days = max(1, min(days, 90))
    logged: list[str] = []

    try:
        recent = fetch_recent_activities(days=days)
        if not recent:
            return []

        conn = sqlite3.connect(HISTORY_DB)
        conn.row_factory = sqlite3.Row
        try:
            for act in sorted(recent, key=lambda a: a["date"]):
                try:
                    msg = _reconcile_activity(conn, act)
                    if msg:
                        logged.append(msg)
                except Exception as e:
                    logged.append(f"Skipped {act.get('name', 'activity')} ({act.get('date', '?')}): {e}")
        finally:
            conn.close()

    except Exception as e:
        logged.append(f"Backfill error: {e}")

    return logged


def insert_planned_workout(
    date_str: str,
    workout_type: str,
    workout_name: str,
    planned_distance_km: float,
    garmin_workout_id: int | None,
    garmin_scheduled_id: int | None,
) -> int:
    """
    Insert a future planned (completed=0) workout row.
    Skips silently if (date, type) already exists.
    Returns the row id (0 if skipped).
    """
    plan = load_plan()
    conn = sqlite3.connect(HISTORY_DB)
    try:
        cycle_id    = None
        phase       = None
        week_number = None
        if plan:
            cycle_id      = _get_or_create_cycle(conn, plan)
            created_on    = date.fromisoformat(plan["created_on"])
            target_date   = date.fromisoformat(date_str)
            weeks_elapsed = (target_date - created_on).days // 7
            week_number   = min(weeks_elapsed + 1, plan["total_weeks"])
            phase         = get_plan_phase(week_number, plan["total_weeks"])

        conn.execute(
            """INSERT OR IGNORE INTO workouts
                   (cycle_id, date, garmin_workout_id, garmin_scheduled_id, workout_name,
                    type, phase, week_number, planned_distance_km, completed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (cycle_id, date_str, garmin_workout_id, garmin_scheduled_id, workout_name,
             workout_type, phase, week_number, planned_distance_km),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def fetch_todays_planned_sessions() -> list[dict]:
    """Return all incomplete planned rows for today."""
    today = date.today().isoformat()
    conn  = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT type, planned_distance_km, workout_name FROM workouts
               WHERE date = ? AND completed = 0 ORDER BY id""",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def generate_week_plan(
    plan: dict,
    week_start_date: date,
    target_km: float,
    load_data: dict | None,
) -> list[dict]:
    """
    For each session returned by plan_week_sessions():
      1. Skip if (date, type) already exists in workouts DB.
      2. Create the Garmin workout template.
      3. Schedule it on the Garmin calendar.
      4. Insert a planned (completed=0) row in the DB.
    Returns a list of summary dicts for created sessions.
    """
    from coaching.plan import plan_week_sessions
    from workouts.workouts import create_and_upload_running_workout
    from garmin.client import get_client

    sessions  = plan_week_sessions(plan, week_start_date, target_km, load_data)
    client    = get_client()
    created   = []

    conn = sqlite3.connect(HISTORY_DB)
    try:
        for sess in sessions:
            date_str = sess["date"]
            wtype    = sess["type"]

            existing = conn.execute(
                "SELECT id FROM workouts WHERE date = ? AND type = ?",
                (date_str, wtype),
            ).fetchone()
            if existing:
                continue

            try:
                upload = create_and_upload_running_workout(
                    sess["workout_name"],
                    sess["description"],
                    sess["steps"],
                )
                garmin_workout_id = upload["workout_id"]
            except Exception as exc:
                created.append({
                    "date": date_str, "type": wtype,
                    "workout_name": sess["workout_name"],
                    "planned_distance_km": sess["planned_distance_km"],
                    "error": f"upload failed: {exc}",
                })
                continue

            garmin_scheduled_id = None
            try:
                sched_result = client.schedule_workout(garmin_workout_id, date_str)
                if isinstance(sched_result, dict):
                    garmin_scheduled_id = (
                        sched_result.get("scheduleId")
                        or sched_result.get("id")
                        or sched_result.get("workoutScheduleId")
                    )
            except Exception:
                pass

            insert_planned_workout(
                date_str=date_str,
                workout_type=wtype,
                workout_name=sess["workout_name"],
                planned_distance_km=sess["planned_distance_km"],
                garmin_workout_id=garmin_workout_id,
                garmin_scheduled_id=garmin_scheduled_id,
            )

            created.append({
                "date":                 date_str,
                "type":                 wtype,
                "workout_name":         sess["workout_name"],
                "planned_distance_km":  sess["planned_distance_km"],
                "garmin_workout_id":    garmin_workout_id,
                "garmin_scheduled_id":  garmin_scheduled_id,
            })
    finally:
        conn.close()

    return created


def log_coach_suggestion(
    trigger: str,
    context_summary: str,
    recommendation: str,
    rationale: str,
    action_type: str | None = None,
) -> int:
    """
    Write a pending coaching suggestion to coach_log (approved=NULL).
    Returns the new row id.
    """
    plan = load_plan()
    conn = sqlite3.connect(HISTORY_DB)
    try:
        cycle_id = None
        if plan:
            row = conn.execute(
                "SELECT id FROM training_cycles WHERE race_date = ?", (plan["race_date"],)
            ).fetchone()
            if row:
                cycle_id = row[0]

        conn.execute(
            """INSERT INTO coach_log
                   (cycle_id, date, trigger, context_summary, recommendation, rationale, action_type, approved)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            (cycle_id, date.today().isoformat(), trigger, context_summary,
             recommendation, rationale, action_type),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def fetch_pending_suggestions() -> list[dict]:
    """Return all coach_log rows where approved IS NULL (not yet reviewed)."""
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT cl.id, cl.date, cl.trigger, cl.context_summary,
                      cl.recommendation, cl.rationale, cl.action_type, cl.created_at,
                      tc.race_name
               FROM coach_log cl
               LEFT JOIN training_cycles tc ON tc.id = cl.cycle_id
               WHERE cl.approved IS NULL
               ORDER BY cl.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def review_suggestion(suggestion_id: int, approved: bool, notes: str | None = None) -> bool:
    """
    Approve (True) or deny (False) a pending coach_log suggestion.
    Returns True if a row was updated, False if the ID wasn't found.
    """
    conn = sqlite3.connect(HISTORY_DB)
    try:
        cursor = conn.execute(
            "UPDATE coach_log SET approved = ?, notes = ? WHERE id = ? AND approved IS NULL",
            (int(approved), notes, suggestion_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# Initialise on import — safe no-op if DB already exists.
try:
    init_history_db()
except Exception as e:
    print(f"[garmin-coach] WARNING: could not initialise history DB: {e}", file=sys.stderr)
