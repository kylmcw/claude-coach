import math
import sqlite3
from datetime import date

from db import HISTORY_DB

# ─── Seed data ────────────────────────────────────────────────────────────────
# Canonical alias → (category, exercise_name) map.
# Loaded once into SQLite on first run; the DB is the source of truth thereafter.
_SEED_MAP: dict[str, tuple[str, str]] = {
    "bench press":              ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "barbell bench press":      ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "barbell bench":            ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "flat bench press":         ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "dumbbell bench press":     ("BENCH_PRESS", "DUMBBELL_BENCH_PRESS"),
    "incline bench press":      ("BENCH_PRESS", "INCLINE_BARBELL_BENCH_PRESS"),
    "incline dumbbell press":   ("BENCH_PRESS", "INCLINE_DUMBBELL_BENCH_PRESS"),
    "decline bench press":      ("BENCH_PRESS", "DECLINE_BARBELL_BENCH_PRESS"),
    "close grip bench press":   ("BENCH_PRESS", "CLOSE_GRIP_BARBELL_BENCH_PRESS"),
    "squat":                    ("SQUAT", "BARBELL_BACK_SQUAT"),
    "back squat":               ("SQUAT", "BARBELL_BACK_SQUAT"),
    "barbell back squat":       ("SQUAT", "BARBELL_BACK_SQUAT"),
    "barbell squat":            ("SQUAT", "BARBELL_BACK_SQUAT"),
    "front squat":              ("SQUAT", "BARBELL_FRONT_SQUAT"),
    "goblet squat":             ("SQUAT", "GOBLET_SQUAT"),
    "dumbbell squat":           ("SQUAT", "DUMBBELL_SQUAT"),
    "bodyweight squat":         ("SQUAT", "BODYWEIGHT_SQUAT"),
    "deadlift":                 ("DEADLIFT", "BARBELL_DEADLIFT"),
    "barbell deadlift":         ("DEADLIFT", "BARBELL_DEADLIFT"),
    "conventional deadlift":    ("DEADLIFT", "BARBELL_DEADLIFT"),
    "romanian deadlift":        ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "rdl":                      ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "sumo deadlift":            ("DEADLIFT", "SUMO_DEADLIFT"),
    "stiff leg deadlift":       ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "overhead press":           ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "ohp":                      ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "shoulder press":           ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "barbell overhead press":   ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "military press":           ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "dumbbell shoulder press":  ("SHOULDER_PRESS", "DUMBBELL_SHOULDER_PRESS"),
    "arnold press":             ("SHOULDER_PRESS", "ARNOLD_PRESS"),
    "row":                      ("ROW", "BENT_OVER_ROW"),
    "barbell row":              ("ROW", "BENT_OVER_ROW"),
    "bent over row":            ("ROW", "BENT_OVER_ROW"),
    "pendlay row":              ("ROW", "BENT_OVER_ROW"),
    "dumbbell row":             ("ROW", "ONE_ARM_DUMBBELL_ROW"),
    "cable row":                ("ROW", "SEATED_CABLE_ROW"),
    "seated cable row":         ("ROW", "SEATED_CABLE_ROW"),
    "t-bar row":                ("ROW", "T_BAR_ROW"),
    "bicep curl":               ("CURL", "BARBELL_BICEPS_CURL"),
    "barbell curl":             ("CURL", "BARBELL_BICEPS_CURL"),
    "dumbbell curl":            ("CURL", "DUMBBELL_BICEPS_CURL"),
    "hammer curl":              ("CURL", "DUMBBELL_HAMMER_CURL"),
    "preacher curl":            ("CURL", "PREACHER_CURL"),
    "tricep extension":         ("TRICEPS_EXTENSION", "OVERHEAD_TRICEPS_EXTENSION"),
    "skull crusher":            ("TRICEPS_EXTENSION", "LYING_TRICEPS_EXTENSION"),
    "tricep pushdown":          ("TRICEPS_EXTENSION", "TRICEPS_PRESSDOWN"),
    "lateral raise":            ("LATERAL_RAISE", "LATERAL_RAISE_WITH_DUMBBELLS"),
    "dumbbell lateral raise":   ("LATERAL_RAISE", "LATERAL_RAISE_WITH_DUMBBELLS"),
    "front raise":              ("LATERAL_RAISE", "FRONT_RAISE"),
    "lunge":                    ("LUNGE", "BARBELL_LUNGE"),
    "barbell lunge":            ("LUNGE", "BARBELL_LUNGE"),
    "dumbbell lunge":           ("LUNGE", "DUMBBELL_LUNGE"),
    "walking lunge":            ("LUNGE", "WALKING_LUNGE"),
    "pull up":                  ("PULL_UP", "PULL_UP"),
    "pullup":                   ("PULL_UP", "PULL_UP"),
    "chin up":                  ("PULL_UP", "CHIN_UP"),
    "chinup":                   ("PULL_UP", "CHIN_UP"),
    "lat pulldown":             ("PULL_UP", "LAT_PULLDOWN"),
    "push up":                  ("PUSH_UP", "PUSH_UP"),
    "pushup":                   ("PUSH_UP", "PUSH_UP"),
    "leg curl":                 ("LEG_CURL", "LEG_CURL"),
    "leg extension":            ("LEG_CURL", "LEG_EXTENSION"),
    "calf raise":               ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "standing calf raise":      ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "seated calf raise":        ("CALF_RAISE", "SEATED_CALF_RAISE"),
    "hip thrust":               ("HIP_RAISE", "BARBELL_HIP_THRUST"),
    "glute bridge":             ("HIP_RAISE", "WEIGHTED_GLUTE_BRIDGE"),
    "shrug":                    ("SHRUG", "BARBELL_SHRUG"),
    "barbell shrug":            ("SHRUG", "BARBELL_SHRUG"),
    "dumbbell shrug":           ("SHRUG", "DUMBBELL_SHRUG"),
    "fly":                      ("FLYE", "DUMBBELL_FLYE"),
    "flye":                     ("FLYE", "DUMBBELL_FLYE"),
    "dumbbell fly":             ("FLYE", "DUMBBELL_FLYE"),
    "cable fly":                ("FLYE", "CABLE_CROSSOVER"),
    "cable crossover":          ("FLYE", "CABLE_CROSSOVER"),
    "plank":                    ("PLANK", "PLANK"),
    "crunch":                   ("CRUNCH", "CRUNCH"),
    "sit up":                   ("SIT_UP", "SIT_UP"),
    "situp":                    ("SIT_UP", "SIT_UP"),
    "leg raise":                ("LEG_RAISE", "HANGING_LEG_RAISE"),
    "hanging leg raise":        ("LEG_RAISE", "HANGING_LEG_RAISE"),
}


def _seed_garmin_exercises(conn: sqlite3.Connection) -> None:
    """Seed garmin_exercises and exercise_aliases from _SEED_MAP if tables are empty."""
    count = conn.execute("SELECT COUNT(*) FROM garmin_exercises").fetchone()[0]
    if count > 0:
        return
    for alias, (category, exercise_name) in _SEED_MAP.items():
        display_name = exercise_name.replace("_", " ").title()
        conn.execute(
            "INSERT OR IGNORE INTO garmin_exercises (category, exercise_name, display_name) VALUES (?,?,?)",
            (category, exercise_name, display_name),
        )
        ex_id = conn.execute(
            "SELECT id FROM garmin_exercises WHERE category=? AND exercise_name=?",
            (category, exercise_name),
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO exercise_aliases (alias, garmin_exercise_id) VALUES (?,?)",
            (alias, ex_id),
        )
    conn.commit()


def _create_exercise_tables(conn: sqlite3.Connection) -> None:
    """Create exercise-related tables and seed. Called from init_history_db."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exercise_defaults (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT NOT NULL UNIQUE,
            weight_kg            REAL,
            sets                 INTEGER,
            reps                 INTEGER,
            garmin_category      TEXT,
            garmin_exercise_name TEXT,
            last_updated         TEXT NOT NULL,
            notes                TEXT
        );
        CREATE TABLE IF NOT EXISTS garmin_exercises (
            id            INTEGER PRIMARY KEY,
            category      TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            UNIQUE(category, exercise_name)
        );
        CREATE TABLE IF NOT EXISTS exercise_aliases (
            id                 INTEGER PRIMARY KEY,
            alias              TEXT NOT NULL UNIQUE,
            garmin_exercise_id INTEGER NOT NULL REFERENCES garmin_exercises(id)
        );
        CREATE TABLE IF NOT EXISTS exercise_completion_log (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_date          TEXT NOT NULL,
            activity_id          INTEGER,
            garmin_exercise_name TEXT NOT NULL,
            display_name         TEXT,
            sets_completed       INTEGER NOT NULL,
            sets_planned         INTEGER,
            weight_kg            REAL,
            fully_completed      INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS exercise_overrides (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL,          -- 'pct' | 'delta'
            value       REAL NOT NULL,
            label       TEXT,
            start_date  TEXT,
            end_date    TEXT,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL
        );
    """)
    conn.commit()
    _seed_garmin_exercises(conn)


def lookup_garmin_exercise(name: str) -> tuple[str | None, str | None]:
    """
    Query exercise_aliases + garmin_exercises for a (category, exercise_name) match.
    Returns (None, None) if not found — Garmin accepts null for custom exercises.
    """
    key = name.strip().lower()
    conn = sqlite3.connect(HISTORY_DB)
    try:
        # Exact alias match
        row = conn.execute(
            """SELECT ge.category, ge.exercise_name
               FROM exercise_aliases ea
               JOIN garmin_exercises ge ON ge.id = ea.garmin_exercise_id
               WHERE ea.alias = ?""",
            (key,),
        ).fetchone()
        if row:
            return row[0], row[1]

        # Substring fallback: pick the longest alias that is a substring of the input
        rows = conn.execute(
            """SELECT ea.alias, ge.category, ge.exercise_name
               FROM exercise_aliases ea
               JOIN garmin_exercises ge ON ge.id = ea.garmin_exercise_id"""
        ).fetchall()
        best_cat, best_name, best_len = None, None, 0
        for alias, cat, ex_name in rows:
            if alias in key and len(alias) > best_len:
                best_cat, best_name, best_len = cat, ex_name, len(alias)

        return (best_cat, best_name) if best_cat else (None, None)
    finally:
        conn.close()


def register_exercise_alias(
    alias: str,
    category: str,
    exercise_name: str,
    display_name: str | None = None,
) -> None:
    """Register a new alias mapping. Creates the exercise row if it doesn't exist."""
    dn = display_name or exercise_name.replace("_", " ").title()
    conn = sqlite3.connect(HISTORY_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO garmin_exercises (category, exercise_name, display_name) VALUES (?,?,?)",
            (category, exercise_name, dn),
        )
        ex_id = conn.execute(
            "SELECT id FROM garmin_exercises WHERE category=? AND exercise_name=?",
            (category, exercise_name),
        ).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO exercise_aliases (alias, garmin_exercise_id) VALUES (?,?)",
            (alias.strip().lower(), ex_id),
        )
        conn.commit()
    finally:
        conn.close()


def _round_weight(weight_kg: float, increasing: bool) -> float:
    """Round to nearest 2.5 kg plate increment."""
    # Absorb float error (e.g. 100 * 1.1 == 110.00000000000001) before snapping to a plate,
    # otherwise a value already on a 2.5 boundary gets bumped an extra increment (110 -> 112.5).
    units = round(weight_kg / 2.5, 6)
    if increasing:
        return math.ceil(units) * 2.5
    else:
        return math.floor(units) * 2.5


def _round_to_plate(weight_kg: float) -> float:
    """Round to the nearest 2.5 kg plate increment (non-directional)."""
    return round(round(weight_kg / 2.5, 6)) * 2.5


def _apply_override(base_kg: float, kind: str, value: float) -> float:
    """Apply an override to a base weight and snap to the nearest plate.

    kind='pct'   → base * (1 + value/100)   (value is a signed percent, e.g. -20)
    kind='delta' → base + value             (value is a signed kg delta, e.g. -10)
    """
    if kind == "pct":
        return _round_to_plate(base_kg * (1 + value / 100.0))
    return _round_to_plate(base_kg + value)


def set_exercise_defaults(exercises: list[dict]) -> list[str]:
    """
    Upsert default weight/sets/reps for one or more exercises.
    Each dict: {name, weight_kg?, sets?, reps?, garmin_category?, garmin_exercise_name?, notes?}
    garmin_category / garmin_exercise_name are pre-resolved by the caller.
    Returns a list of confirmation strings.
    """
    today = date.today().isoformat()
    conn  = sqlite3.connect(HISTORY_DB)
    results: list[str] = []
    try:
        for ex in exercises:
            name                 = ex["name"].strip()
            weight_kg            = ex.get("weight_kg")
            sets                 = ex.get("sets")
            reps                 = ex.get("reps")
            garmin_category      = ex.get("garmin_category")
            garmin_exercise_name = ex.get("garmin_exercise_name")
            notes                = ex.get("notes")

            existing = conn.execute(
                "SELECT weight_kg, sets, reps FROM exercise_defaults WHERE name = ?", (name,)
            ).fetchone()

            conn.execute(
                """INSERT INTO exercise_defaults
                       (name, weight_kg, sets, reps, garmin_category, garmin_exercise_name, last_updated, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       weight_kg            = COALESCE(excluded.weight_kg, weight_kg),
                       sets                 = COALESCE(excluded.sets, sets),
                       reps                 = COALESCE(excluded.reps, reps),
                       garmin_category      = COALESCE(excluded.garmin_category, garmin_category),
                       garmin_exercise_name = COALESCE(excluded.garmin_exercise_name, garmin_exercise_name),
                       last_updated         = excluded.last_updated,
                       notes                = COALESCE(excluded.notes, notes)""",
                (name, weight_kg, sets, reps, garmin_category, garmin_exercise_name, today, notes),
            )

            if existing:
                old_w = existing[0]
                change = f"  {old_w}kg → {weight_kg}kg" if weight_kg and old_w != weight_kg else ""
                results.append(f"Updated: {name}{change}")
            else:
                parts = []
                if weight_kg is not None:
                    parts.append(f"{weight_kg}kg")
                if sets and reps:
                    parts.append(f"{sets}×{reps}")
                results.append(f"Set: {name} — {', '.join(parts)}" if parts else f"Set: {name}")

        conn.commit()
    finally:
        conn.close()
    return results


def _active_override_row(conn: sqlite3.Connection, name: str, on_date: str) -> dict | None:
    """Return the newest active override for `name` whose window contains `on_date`, else None.

    No stacking — when several overrides are active for one exercise, the most recently
    created (highest id) wins. The window is inclusive; NULL start/end means open-ended.
    """
    row = conn.execute(
        """SELECT id, name, kind, value, label, start_date, end_date, active
             FROM exercise_overrides
            WHERE name = ? AND active = 1
              AND (start_date IS NULL OR start_date <= ?)
              AND (end_date   IS NULL OR end_date   >= ?)
            ORDER BY id DESC
            LIMIT 1""",
        (name, on_date, on_date),
    ).fetchone()
    return dict(row) if row else None


def get_exercise_defaults(name: str | None = None, on_date: str | None = None) -> list[dict]:
    """Return all exercise defaults, or a specific one if name is given.

    Each row gains two derived fields:
      active_override     – the override dict in effect on `on_date` (today by default), or None
      effective_weight_kg – base weight with the active override applied; equals weight_kg when
                            no override is active. This is the weight to program for the session.
    """
    today = on_date or date.today().isoformat()
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    try:
        if name:
            rows = conn.execute(
                "SELECT * FROM exercise_defaults WHERE name = ?", (name,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM exercise_defaults ORDER BY name"
            ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            base = d.get("weight_kg")
            override = _active_override_row(conn, d["name"], today)
            if override is not None and base is not None:
                d["effective_weight_kg"] = _apply_override(
                    float(base), override["kind"], float(override["value"])
                )
            else:
                d["effective_weight_kg"] = base
            d["active_override"] = override
            result.append(d)
        return result
    finally:
        conn.close()


def set_exercise_override(
    name: str,
    kind: str,
    value: float,
    label: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Create an interchangeable override on an exercise's programmed weight.

    Modifies the weight a session is built with WITHOUT touching the stored base default —
    when the window passes (or the override is cleared) the programmed weight reverts to base.

    kind='pct'   → effective = base * (1 + value/100)   (value signed, e.g. -20 for a sick week)
    kind='delta' → effective = base + value             (value signed kg)

    No stacking — the newest active override for an exercise wins. Returns a summary dict
    including the resolved effective weight for `start_date` (or today).
    """
    kind = kind.strip().lower()
    if kind not in ("pct", "delta"):
        raise ValueError(f"override kind must be 'pct' or 'delta', got '{kind}'")
    name = name.strip()
    today = date.today().isoformat()
    conn = sqlite3.connect(HISTORY_DB)
    try:
        conn.execute(
            """INSERT INTO exercise_overrides
                   (name, kind, value, label, start_date, end_date, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (name, kind, float(value), label, start_date, end_date, today),
        )
        conn.commit()
    finally:
        conn.close()

    # Resolve the effective weight for the window start (or today) for the confirmation message.
    ref_date = start_date or today
    rows = get_exercise_defaults(name, on_date=ref_date)
    base      = rows[0]["weight_kg"]            if rows else None
    effective = rows[0]["effective_weight_kg"]  if rows else None
    return {
        "name":       name,
        "kind":       kind,
        "value":      float(value),
        "label":      label,
        "start_date": start_date,
        "end_date":   end_date,
        "base_kg":    base,
        "effective_kg": effective,
    }


def clear_exercise_override(
    name: str | None = None,
    label: str | None = None,
    clear_all: bool = False,
) -> int:
    """Deactivate active overrides. Returns the number cleared.

    Specify exactly one selector: clear_all=True (all), label=..., or name=...
    Rows are soft-deactivated (active=0) so the override history is preserved.
    """
    conn = sqlite3.connect(HISTORY_DB)
    try:
        if clear_all:
            cur = conn.execute("UPDATE exercise_overrides SET active = 0 WHERE active = 1")
        elif label:
            cur = conn.execute(
                "UPDATE exercise_overrides SET active = 0 WHERE active = 1 AND label = ?",
                (label,),
            )
        elif name:
            cur = conn.execute(
                "UPDATE exercise_overrides SET active = 0 WHERE active = 1 AND name = ?",
                (name.strip(),),
            )
        else:
            raise ValueError("clear_exercise_override needs one of: name, label, or clear_all=True")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def log_strength_progress(
    exercises: list[dict],
    apply_changes: bool = True,
) -> dict:
    """
    Log per-exercise session outcome and compute progression suggestions.

    Each dict: {name, completed: bool, notes?: str}
    - completed=True  → +10% weight, rounded up to nearest 2.5 kg
    - completed=False → -10% weight, rounded down to nearest 2.5 kg

    If apply_changes=True, updates exercise_defaults immediately.
    Returns {"suggestions": [...], "applied": bool}.
    """
    today       = date.today().isoformat()
    suggestions = []
    conn        = sqlite3.connect(HISTORY_DB)
    try:
        for ex in exercises:
            name      = ex["name"].strip()
            completed = bool(ex.get("completed", True))
            notes     = ex.get("notes", "")

            row = conn.execute(
                "SELECT weight_kg, sets, reps FROM exercise_defaults WHERE name = ?", (name,)
            ).fetchone()

            if row is None or row[0] is None:
                suggestions.append({
                    "exercise": name,
                    "outcome":  "completed" if completed else "failed",
                    "note":     "No default weight set — use set_exercise_defaults first.",
                    "current_kg":   None,
                    "suggested_kg": None,
                })
                continue

            current_kg = float(row[0])
            if completed:
                # Ratchet up: a completed session raises the stored default.
                new_kg = _round_weight(current_kg * 1.10, increasing=True)
                suggestions.append({
                    "exercise":     name,
                    "outcome":      "completed",
                    "current_kg":   current_kg,
                    "suggested_kg": new_kg,
                    "action":       f"+10% → {new_kg}kg",
                    "needs_review": False,
                    "notes":        notes,
                })
                if apply_changes:
                    conn.execute(
                        """UPDATE exercise_defaults
                              SET weight_kg = ?, last_updated = ?, notes = COALESCE(?, notes)
                            WHERE name = ?""",
                        (new_kg, today, notes or None, name),
                    )
            else:
                # Ratchet guarantee: a failed/off session NEVER silently lowers the stored
                # default (sick days were corrupting base capacity). Flag for explicit review
                # instead — the dispatcher logs a pending coach suggestion. Default unchanged.
                suggestions.append({
                    "exercise":     name,
                    "outcome":      "failed",
                    "current_kg":   current_kg,
                    "suggested_kg": None,
                    "action":       f"held at {current_kg}kg (default not lowered — logged for review)",
                    "needs_review": True,
                    "notes":        notes,
                })

        if apply_changes:
            conn.commit()
    finally:
        conn.close()

    return {"suggestions": suggestions, "applied": apply_changes}


def log_exercise_completions(activity_id: int, exercises: list[dict]) -> None:
    """
    Persist per-exercise completion for one activity. Idempotent — skips if
    activity_id already logged.

    Each dict: {garmin_exercise_name, display_name, sets_completed, sets_planned?,
                weight_kg?, fully_completed: bool}
    """
    today = date.today().isoformat()
    conn  = sqlite3.connect(HISTORY_DB)
    try:
        existing = conn.execute(
            "SELECT COUNT(*) FROM exercise_completion_log WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()[0]
        if existing > 0:
            return
        for ex in exercises:
            conn.execute(
                """INSERT INTO exercise_completion_log
                       (logged_date, activity_id, garmin_exercise_name, display_name,
                        sets_completed, sets_planned, weight_kg, fully_completed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today,
                    activity_id,
                    ex.get("garmin_exercise_name") or "",
                    ex.get("display_name") or "",
                    int(ex["sets_completed"]),
                    ex.get("sets_planned"),
                    ex.get("weight_kg"),
                    1 if ex.get("fully_completed") else 0,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def check_progression_due(garmin_exercise_name: str, display_name: str = "") -> dict:
    """
    Return progression status for an exercise based on the last 3 logged sessions.

    Trigger: 2 of the last 3 sessions were fully_completed.

    Returns:
      {"due": bool, "completions": int, "sessions": int, "suggestion_kg": float | None}
    """
    conn = sqlite3.connect(HISTORY_DB)
    try:
        rows = conn.execute(
            """SELECT fully_completed, weight_kg
               FROM exercise_completion_log
               WHERE garmin_exercise_name = ?
                  OR (garmin_exercise_name = '' AND display_name = ?)
               ORDER BY logged_date DESC, id DESC
               LIMIT 3""",
            (garmin_exercise_name, display_name),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return {"due": False, "completions": 0, "sessions": len(rows), "suggestion_kg": None}

    completions = sum(1 for r in rows if r[0])
    due         = completions >= 2

    suggestion_kg = None
    if due:
        weights = [r[1] for r in rows if r[1] is not None]
        if weights:
            suggestion_kg = _round_weight(weights[0] + 2.5, increasing=True)

    return {
        "due":           due,
        "completions":   completions,
        "sessions":      len(rows),
        "suggestion_kg": suggestion_kg,
    }
