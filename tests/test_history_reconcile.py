import sqlite3
from datetime import date, timedelta

import db.history as history


def _act(activity_id, d, sport="running", name="Morning Run"):
    """A processed activity dict shaped like fetch_recent_activities() output."""
    return {
        "activity_id": activity_id,
        "date": d,
        "name": name,
        "type": sport,
        "distance_km": 8.0,
        "duration_min": 45.0,
        "avg_hr": 150,
        "avg_pace": "5:37/km",
        "aerobic_te": 3.0,
        "anaerobic_te": 0.5,
        "activity_training_load": 80.0,
        "avg_cadence": 170.0,
        "ground_contact_time_ms": 240.0,
        "ground_contact_balance_left": 49.5,
        "vertical_oscillation_cm": 8.0,
        "vertical_ratio_pct": 7.0,
        "elevation_gain_m": 30.0,
        "avg_respiration_rate": 16.0,
    }


def _orphan_feedback_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE workout_id NOT IN (SELECT id FROM workouts)"
        ).fetchone()[0]
    finally:
        conn.close()


def test_same_day_duplicate_creates_no_orphan_feedback(history_db, monkeypatch):
    """Regression for the INSERT OR IGNORE + last_insert_rowid()=0 orphan-feedback bug."""
    today = date.today().isoformat()
    monkeypatch.setattr(history, "fetch_recent_activities",
                        lambda days=7: [_act(1, today), _act(2, today)])

    history.backfill_runs(14)

    conn = sqlite3.connect(history_db)
    try:
        same_day = conn.execute(
            "SELECT COUNT(*) FROM workouts WHERE date=?", (today,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert same_day == 1                       # (date, type) unique index keeps one row
    assert _orphan_feedback_count(history_db) == 0  # no feedback pointing at workout_id=0


def test_backfill_is_idempotent(history_db, monkeypatch):
    d1 = (date.today() - timedelta(days=2)).isoformat()
    d2 = (date.today() - timedelta(days=1)).isoformat()
    monkeypatch.setattr(history, "fetch_recent_activities",
                        lambda days=7: [_act(10, d1), _act(11, d2)])

    first = history.backfill_runs(14)
    second = history.backfill_runs(14)

    conn = sqlite3.connect(history_db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]
    finally:
        conn.close()

    assert sum(m.startswith("Logged") for m in first) == 2
    assert second == []      # already logged -> nothing re-inserted
    assert total == 2


def test_backfill_updates_matching_planned_row(history_db, monkeypatch):
    d = (date.today() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(history_db)
    try:
        conn.execute("INSERT INTO workouts(date, type, completed) VALUES(?,?,0)", (d, "easy_run"))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(history, "fetch_recent_activities", lambda days=7: [_act(20, d)])
    history.backfill_runs(14)

    conn = sqlite3.connect(history_db)
    try:
        rows = conn.execute(
            "SELECT completed, activity_id FROM workouts WHERE date=?", (d,)
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1        # planned row updated in place, not duplicated
    assert rows[0][0] == 1       # marked completed
    assert rows[0][1] == 20      # actual activity_id filled in


def test_backfill_skips_bad_activity_without_aborting(history_db, monkeypatch):
    """One malformed activity must not stop the rest of the window from logging."""
    good = _act(30, date.today().isoformat())
    bad = {"activity_id": 31, "date": date.today().isoformat(), "type": "running"}  # missing fields
    monkeypatch.setattr(history, "fetch_recent_activities", lambda days=7: [bad, good])

    out = history.backfill_runs(14)

    assert any(m.startswith("Logged") for m in out)   # good one still logged
    assert any(m.startswith("Skipped") for m in out)  # bad one skipped, not fatal
