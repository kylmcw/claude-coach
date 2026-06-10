import os

from garminconnect import Garmin

# ─── Cached client (reused across tool calls in the same server session) ─────
_client = None


def get_client() -> Garmin:
    global _client
    if _client is None:
        _client = Garmin(
            os.environ["GARMIN_EMAIL"],
            os.environ["GARMIN_PASSWORD"]
        )
        _client.login()
    return _client
