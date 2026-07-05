"""
Tests for db.history.assess_recent_feedback — the logged RPE/feel/niggle scanner
that feeds weekly/monthly reviews and week-planning back-off.

Uses the isolated `history_db` fixture (real schema, no plan) from conftest.
"""
from datetime import date, timedelta

import db.history as history


def _log(db, days_ago, wtype, rpe=None, feel=None, niggles=None):
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    history.log_workout_to_history(
        d, None, None, wtype, None, 8.0, "5:00/km", 150, True,
        rpe, feel, niggles, None,
    )


def test_no_feedback_is_no_backoff(history_db):
    fb = history.assess_recent_feedback()
    assert fb["backoff"] is False
    assert fb["reason"] is None
    assert fb["niggle_warnings"] == []


def test_recurring_niggle_triggers_backoff(history_db):
    for i in range(3):
        _log(history_db, i + 1, f"easy_run_{i}", niggles="left calf tight")
    fb = history.assess_recent_feedback()
    assert fb["backoff"] is True
    assert any("calf" in w for w in fb["niggle_warnings"])
    assert "calf" in (fb["reason"] or "")


def test_two_niggles_not_enough(history_db):
    for i in range(2):
        _log(history_db, i + 1, f"easy_run_{i}", niggles="calf niggle")
    fb = history.assess_recent_feedback()
    assert fb["niggle_warnings"] == []
    assert fb["backoff"] is False


def test_old_niggles_outside_window_ignored(history_db):
    for i in range(3):
        _log(history_db, 20 + i, f"easy_run_{i}", niggles="calf sore")
    fb = history.assess_recent_feedback()
    assert fb["niggle_warnings"] == []


def test_high_rpe_trend_triggers_backoff(history_db):
    for i in range(3):
        _log(history_db, i + 1, f"quality_{i}", rpe=9)
    fb = history.assess_recent_feedback()
    assert fb["high_rpe_count"] == 3
    assert fb["backoff"] is True
    assert "RPE" in (fb["reason"] or "").upper() or "rpe" in (fb["reason"] or "")


def test_avg_rpe_reported_in_lines(history_db):
    _log(history_db, 1, "easy_a", rpe=5)
    _log(history_db, 2, "easy_b", rpe=6)
    fb = history.assess_recent_feedback()
    assert fb["avg_rpe"] == 5.5
    assert any("RPE" in l for l in fb["lines"])


def test_rough_feel_counts(history_db):
    _log(history_db, 1, "easy_a", feel="rough")
    _log(history_db, 2, "easy_b", feel="flat and tired")
    _log(history_db, 3, "easy_c", feel="great")
    fb = history.assess_recent_feedback()
    assert fb["rough_feel_count"] == 2
