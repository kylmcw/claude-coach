from pathlib import Path

from utils import get_profile_suffix

HISTORY_DB = Path.home() / f".garmin-coach{get_profile_suffix()}-history.db"
