from datetime import date, timedelta

import garmin.training as training


class _FakeClient:
    def __init__(self, activities):
        self._acts = activities
        self.calls = 0

    def get_activities(self, start, batch):
        self.calls += 1
        return self._acts[start:start + batch]


def _raw(i, d):
    """A raw Garmin activity dict shaped like client.get_activities() output."""
    return {
        "startTimeLocal": f"{d} 07:00:00",
        "activityId": i,
        "activityName": "Run",
        "activityType": {"typeKey": "running"},
        "distance": 8000,
        "duration": 2400,
        "averageHR": 150,
    }


def test_paging_collects_beyond_one_batch(monkeypatch):
    """25 in-window activities with a batch of 20 must all come back (it pages)."""
    today = date.today().isoformat()
    fake = _FakeClient([_raw(i, today) for i in range(25)])
    monkeypatch.setattr(training, "get_client", lambda: fake)

    out = training.fetch_recent_activities(days=5)  # batch = max(20, 10) = 20

    assert len(out) == 25     # a single fixed fetch of 20 would have dropped 5
    assert fake.calls >= 2    # proof it paged


def test_paging_stops_at_cutoff(monkeypatch):
    recent = date.today().isoformat()
    old = (date.today() - timedelta(days=40)).isoformat()
    fake = _FakeClient([_raw(i, recent) for i in range(20)] + [_raw(100 + i, old) for i in range(20)])
    monkeypatch.setattr(training, "get_client", lambda: fake)

    out = training.fetch_recent_activities(days=5)

    assert len(out) == 20                              # only in-window kept
    assert all(a["date"] == recent for a in out)
    assert fake.calls == 2                              # page 2 hits the cutoff, then stop
