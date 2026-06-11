"""
Unit tests for server/db/exercises.py

Covers:
- _seed_garmin_exercises idempotency
- lookup_garmin_exercise: exact alias, substring fallback, unknown
- register_exercise_alias: new exercise + new alias, re-register alias to different exercise
- set_exercise_defaults + get_exercise_defaults round-trips (insert, update, partial update)
- log_strength_progress: +10%/-10% weight progression, apply_changes flag
- log_exercise_completions: basic insert, idempotency
- check_progression_due: < 2 sessions (not due), >= 2 of 3 completions (due), suggestion_kg
"""
import sqlite3
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import db.exercises as exercises  # noqa: E402


# ─── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ex_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the exercise tables seeded, no real home-dir file."""
    db_path = tmp_path / "exercises_test.db"
    monkeypatch.setattr(exercises, "HISTORY_DB", db_path)

    # Build the schema and seed data exactly as production does
    conn = sqlite3.connect(db_path)
    exercises._create_exercise_tables(conn)
    conn.close()

    return db_path


def _conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


# ─── Seeding ──────────────────────────────────────────────────────────────────

def test_seed_populates_tables(ex_db):
    with _conn(ex_db) as c:
        n_ex    = c.execute("SELECT COUNT(*) FROM garmin_exercises").fetchone()[0]
        n_alias = c.execute("SELECT COUNT(*) FROM exercise_aliases").fetchone()[0]
    assert n_ex > 0
    assert n_alias >= len(exercises._SEED_MAP)


def test_seed_is_idempotent(ex_db):
    """Calling _seed_garmin_exercises a second time must not add rows."""
    with _conn(ex_db) as c:
        before_ex    = c.execute("SELECT COUNT(*) FROM garmin_exercises").fetchone()[0]
        before_alias = c.execute("SELECT COUNT(*) FROM exercise_aliases").fetchone()[0]
        exercises._seed_garmin_exercises(c)
        after_ex    = c.execute("SELECT COUNT(*) FROM garmin_exercises").fetchone()[0]
        after_alias = c.execute("SELECT COUNT(*) FROM exercise_aliases").fetchone()[0]

    assert after_ex    == before_ex
    assert after_alias == before_alias


# ─── lookup_garmin_exercise ───────────────────────────────────────────────────

def test_lookup_exact_alias(ex_db):
    cat, name = exercises.lookup_garmin_exercise("squat")
    assert cat  == "SQUAT"
    assert name == "BARBELL_BACK_SQUAT"


def test_lookup_case_insensitive(ex_db):
    cat, name = exercises.lookup_garmin_exercise("DEADLIFT")
    assert cat  == "DEADLIFT"
    assert name == "BARBELL_DEADLIFT"


def test_lookup_unknown_returns_none_none(ex_db):
    cat, name = exercises.lookup_garmin_exercise("telekinetic lift")
    assert cat  is None
    assert name is None


def test_lookup_substring_fallback(ex_db):
    """'dumbbell bench press' should match the longer alias over just 'bench press'."""
    cat, name = exercises.lookup_garmin_exercise("dumbbell bench press heavy")
    assert cat  == "BENCH_PRESS"
    assert name == "DUMBBELL_BENCH_PRESS"


def test_lookup_rdl_alias(ex_db):
    """Short alias 'rdl' maps to Romanian deadlift."""
    cat, name = exercises.lookup_garmin_exercise("rdl")
    assert cat  == "DEADLIFT"
    assert name == "ROMANIAN_DEADLIFT"


# ─── register_exercise_alias ─────────────────────────────────────────────────

def test_register_new_alias_for_existing_exercise(ex_db):
    exercises.register_exercise_alias(
        "bb squat",
        "SQUAT",
        "BARBELL_BACK_SQUAT",
    )
    cat, name = exercises.lookup_garmin_exercise("bb squat")
    assert cat  == "SQUAT"
    assert name == "BARBELL_BACK_SQUAT"


def test_register_creates_new_exercise_row(ex_db):
    exercises.register_exercise_alias(
        "nordic curl",
        "LEG_CURL",
        "NORDIC_HAMSTRING_CURL",
        display_name="Nordic Hamstring Curl",
    )
    cat, name = exercises.lookup_garmin_exercise("nordic curl")
    assert cat  == "LEG_CURL"
    assert name == "NORDIC_HAMSTRING_CURL"


def test_register_alias_replace(ex_db):
    """Re-registering an alias to a different exercise overwrites the mapping."""
    exercises.register_exercise_alias("row", "PULL_UP", "LAT_PULLDOWN")
    cat, name = exercises.lookup_garmin_exercise("row")
    assert cat  == "PULL_UP"
    assert name == "LAT_PULLDOWN"


# ─── set_exercise_defaults / get_exercise_defaults ───────────────────────────

def test_set_and_get_exercise_defaults_round_trip(ex_db):
    msgs = exercises.set_exercise_defaults([
        {"name": "Squat", "weight_kg": 100.0, "sets": 5, "reps": 5,
         "garmin_category": "SQUAT", "garmin_exercise_name": "BARBELL_BACK_SQUAT"}
    ])
    assert any("Set: Squat" in m for m in msgs)

    rows = exercises.get_exercise_defaults("Squat")
    assert len(rows) == 1
    r = rows[0]
    assert r["weight_kg"]            == 100.0
    assert r["sets"]                 == 5
    assert r["reps"]                 == 5
    assert r["garmin_category"]      == "SQUAT"
    assert r["garmin_exercise_name"] == "BARBELL_BACK_SQUAT"


def test_set_exercise_defaults_update_shows_weight_change(ex_db):
    exercises.set_exercise_defaults([{"name": "Bench Press", "weight_kg": 80.0, "sets": 3, "reps": 8}])
    msgs = exercises.set_exercise_defaults([{"name": "Bench Press", "weight_kg": 85.0}])
    assert any("80.0kg → 85.0kg" in m for m in msgs)

    rows = exercises.get_exercise_defaults("Bench Press")
    assert rows[0]["weight_kg"] == 85.0


def test_set_exercise_defaults_partial_update_preserves_existing(ex_db):
    """Updating only weight_kg must not overwrite existing sets/reps (COALESCE logic)."""
    exercises.set_exercise_defaults([{"name": "OHP", "weight_kg": 60.0, "sets": 4, "reps": 6}])
    exercises.set_exercise_defaults([{"name": "OHP", "weight_kg": 62.5}])

    rows = exercises.get_exercise_defaults("OHP")
    r = rows[0]
    assert r["weight_kg"] == 62.5
    assert r["sets"]      == 4
    assert r["reps"]      == 6


def test_get_all_exercise_defaults_ordered(ex_db):
    exercises.set_exercise_defaults([
        {"name": "Deadlift", "weight_kg": 120.0, "sets": 3, "reps": 5},
        {"name": "Bench Press", "weight_kg": 80.0, "sets": 3, "reps": 8},
    ])
    rows = exercises.get_exercise_defaults()
    names = [r["name"] for r in rows]
    assert names == sorted(names)


def test_get_exercise_defaults_empty_for_unknown(ex_db):
    rows = exercises.get_exercise_defaults("Unknown Exercise")
    assert rows == []


# ─── log_strength_progress ────────────────────────────────────────────────────

def test_log_strength_progress_completed_increases_weight(ex_db):
    exercises.set_exercise_defaults([{"name": "Squat", "weight_kg": 100.0, "sets": 5, "reps": 5}])
    result = exercises.log_strength_progress([{"name": "Squat", "completed": True}])

    assert result["applied"] is True
    suggestions = result["suggestions"]
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["outcome"]      == "completed"
    assert s["current_kg"]   == 100.0
    assert s["suggested_kg"] == 110.0   # ceil(100 * 1.1 / 2.5) * 2.5 = 110.0

    rows = exercises.get_exercise_defaults("Squat")
    assert rows[0]["weight_kg"] == 110.0


def test_log_strength_progress_failed_decreases_weight(ex_db):
    exercises.set_exercise_defaults([{"name": "Deadlift", "weight_kg": 120.0, "sets": 3, "reps": 5}])
    result = exercises.log_strength_progress([{"name": "Deadlift", "completed": False}])

    s = result["suggestions"][0]
    assert s["outcome"]      == "failed"
    assert s["suggested_kg"] == 107.5   # floor(120 * 0.9 / 2.5) * 2.5 = 107.5

    rows = exercises.get_exercise_defaults("Deadlift")
    assert rows[0]["weight_kg"] == 107.5


def test_log_strength_progress_apply_changes_false(ex_db):
    """With apply_changes=False the DB must not be mutated."""
    exercises.set_exercise_defaults([{"name": "Press", "weight_kg": 50.0, "sets": 3, "reps": 10}])
    result = exercises.log_strength_progress(
        [{"name": "Press", "completed": True}], apply_changes=False
    )

    assert result["applied"] is False
    rows = exercises.get_exercise_defaults("Press")
    assert rows[0]["weight_kg"] == 50.0   # unchanged


def test_log_strength_progress_no_default_set(ex_db):
    """Exercises without a default weight get a graceful note, not an error."""
    result = exercises.log_strength_progress([{"name": "Mystery Lift", "completed": True}])
    s = result["suggestions"][0]
    assert s["suggested_kg"] is None
    assert "No default weight" in s["note"]


def test_round_weight_increasing():
    assert exercises._round_weight(101.0, increasing=True)  == 102.5
    assert exercises._round_weight(100.0, increasing=True)  == 100.0
    assert exercises._round_weight(100.1, increasing=True)  == 102.5


def test_round_weight_decreasing():
    assert exercises._round_weight(101.0, increasing=False) == 100.0
    assert exercises._round_weight(100.0, increasing=False) == 100.0
    assert exercises._round_weight(99.9,  increasing=False) == 97.5


# ─── log_exercise_completions ─────────────────────────────────────────────────

def test_log_exercise_completions_basic(ex_db):
    exercises.log_exercise_completions(
        activity_id=42,
        exercises=[
            {"garmin_exercise_name": "BARBELL_BACK_SQUAT", "display_name": "Squat",
             "sets_completed": 3, "sets_planned": 5, "weight_kg": 100.0, "fully_completed": True},
        ],
    )
    with _conn(ex_db) as c:
        rows = c.execute(
            "SELECT * FROM exercise_completion_log WHERE activity_id = 42"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["fully_completed"] == 1
    assert rows[0]["weight_kg"]       == 100.0


def test_log_exercise_completions_idempotent(ex_db):
    """Calling twice with the same activity_id must not insert duplicate rows."""
    payload = [
        {"garmin_exercise_name": "BARBELL_DEADLIFT", "display_name": "Deadlift",
         "sets_completed": 3, "sets_planned": 3, "weight_kg": 120.0, "fully_completed": True},
    ]
    exercises.log_exercise_completions(99, payload)
    exercises.log_exercise_completions(99, payload)

    with _conn(ex_db) as c:
        count = c.execute(
            "SELECT COUNT(*) FROM exercise_completion_log WHERE activity_id = 99"
        ).fetchone()[0]
    assert count == 1


# ─── check_progression_due ───────────────────────────────────────────────────

def test_check_progression_not_due_with_fewer_than_two_sessions(ex_db):
    result = exercises.check_progression_due("BARBELL_BACK_SQUAT")
    assert result["due"]     is False
    assert result["sessions"] == 0


def test_check_progression_not_due_with_one_completion_of_three(ex_db):
    exercises.log_exercise_completions(
        1, [{"garmin_exercise_name": "PRESS", "display_name": "Press",
             "sets_completed": 3, "sets_planned": 3, "weight_kg": 60.0, "fully_completed": True}]
    )
    exercises.log_exercise_completions(
        2, [{"garmin_exercise_name": "PRESS", "display_name": "Press",
             "sets_completed": 2, "sets_planned": 3, "weight_kg": 60.0, "fully_completed": False}]
    )
    exercises.log_exercise_completions(
        3, [{"garmin_exercise_name": "PRESS", "display_name": "Press",
             "sets_completed": 2, "sets_planned": 3, "weight_kg": 60.0, "fully_completed": False}]
    )
    result = exercises.check_progression_due("PRESS")
    assert result["due"]         is False
    assert result["completions"] == 1


def test_check_progression_due_with_two_of_three_completions(ex_db):
    exercises.log_exercise_completions(
        10, [{"garmin_exercise_name": "ROW", "display_name": "Row",
              "sets_completed": 3, "sets_planned": 3, "weight_kg": 80.0, "fully_completed": True}]
    )
    exercises.log_exercise_completions(
        11, [{"garmin_exercise_name": "ROW", "display_name": "Row",
              "sets_completed": 3, "sets_planned": 3, "weight_kg": 80.0, "fully_completed": True}]
    )
    exercises.log_exercise_completions(
        12, [{"garmin_exercise_name": "ROW", "display_name": "Row",
              "sets_completed": 2, "sets_planned": 3, "weight_kg": 80.0, "fully_completed": False}]
    )
    result = exercises.check_progression_due("ROW")
    assert result["due"]           is True
    assert result["completions"]   == 2
    assert result["suggestion_kg"] == 82.5   # 80 + 2.5, already a plate increment


def test_check_progression_suggestion_kg_rounds_up(ex_db):
    """Suggestion should be current weight + 2.5, rounded to nearest 2.5 (ceil)."""
    exercises.log_exercise_completions(
        20, [{"garmin_exercise_name": "CURL", "display_name": "Curl",
              "sets_completed": 3, "sets_planned": 3, "weight_kg": 14.0, "fully_completed": True}]
    )
    exercises.log_exercise_completions(
        21, [{"garmin_exercise_name": "CURL", "display_name": "Curl",
              "sets_completed": 3, "sets_planned": 3, "weight_kg": 14.0, "fully_completed": True}]
    )
    result = exercises.check_progression_due("CURL")
    assert result["due"]           is True
    assert result["suggestion_kg"] == 17.5   # 14 + 2.5 = 16.5 → ceil to 17.5
