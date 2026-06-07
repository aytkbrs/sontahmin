from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import BASE_URL, COMPETITIONS_PATH, LIVE_SCORE_WIDGET_PATH, STATISTICS_BASE_URL, football_bulletin_path


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}


@dataclass(slots=True)
class IddaaClient:
    base_url: str = BASE_URL

    def _get_json(self, path: str) -> dict:
        request = Request(
            url=f"{self.base_url}{path}",
            headers=DEFAULT_HEADERS,
            method="GET",
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"HTTP error while fetching {path}: {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error while fetching {path}: {exc.reason}") from exc

    def fetch_football_bulletin(self, bulletin_type: int) -> dict:
        return self._get_json(football_bulletin_path(bulletin_type))

    def fetch_competitions(self) -> dict:
        return self._get_json(COMPETITIONS_PATH)

    def fetch_live_score_widget(self) -> dict:
        return self._get_json(LIVE_SCORE_WIDGET_PATH)


@dataclass(slots=True)
class StatsClient:
    base_url: str = STATISTICS_BASE_URL

    def _get_json(self, path: str) -> dict:
        request = Request(
            url=f"{self.base_url}{path}",
            headers=DEFAULT_HEADERS,
            method="GET",
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} fetching {path}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error fetching {path}: {exc.reason}") from exc

    def fetch_card_corners(self, event_id: int) -> dict:
        return self._get_json(f"/statistics/soccer/card-corners/{event_id}")

    def fetch_match_card(self, event_id: int) -> dict:
        return self._get_json(f"/statistics/soccer/match-card/{event_id}")
