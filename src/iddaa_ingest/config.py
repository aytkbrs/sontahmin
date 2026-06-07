from __future__ import annotations

from pathlib import Path


BASE_URL = "https://sportsbookv2.iddaa.com"
STATISTICS_BASE_URL = "https://statisticsv2.iddaa.com"
COMPETITIONS_PATH = "/sportsbook/competitions"
LIVE_SCORE_WIDGET_PATH = "/sportsbook/live-events-for-widget"

SPORT_ID_FOOTBALL = 1
BULLETIN_TYPE_PREMATCH = 0
BULLETIN_TYPE_LIVE = 1
BULLETIN_TYPE_LONGTERM = 2

EVENT_STATUS_NOT_STARTED = 0
EVENT_STATUS_LIVE = 1
EVENT_STATUS_SUSPENDED = 2
EVENT_STATUS_ENDED = 3
EVENT_STATUS_CLOSED = 4


def football_bulletin_path(bulletin_type: int) -> str:
    return f"/sportsbook/events?st={SPORT_ID_FOOTBALL}&type={bulletin_type}&version=0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "iddaa.sqlite3"
