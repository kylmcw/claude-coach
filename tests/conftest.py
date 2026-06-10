import sys
from pathlib import Path

# The server uses bare imports (server/ is on sys.path at runtime via
# `python server/main.py`). Mirror that here so tests can `import db.history` etc.
SERVER = Path(__file__).resolve().parent.parent / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import pytest  # noqa: E402
import db.history as history  # noqa: E402


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    """An isolated history DB (real schema, no training plan) for reconcile/backfill tests."""
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(history, "HISTORY_DB", db_path)
    monkeypatch.setattr(history, "load_plan", lambda: None)  # no cycle linkage in tests
    history.init_history_db()
    return db_path
