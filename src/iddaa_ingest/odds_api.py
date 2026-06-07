"""The Odds API client — Pinnacle ve diğer keskin bahisçi oranlarını çeker.

Kullanım amacı: iddaa oranları ile Pinnacle oranlarını karşılaştırıp gerçek edge bulmak.
Eğer iddaa 2.50 veriyor, Pinnacle 2.20 veriyorsa → %14 değer fark var.

Kredi yönetimi: Starter plan 500 kredi/ay.
- Her sport_key çağrısı = 1 kredi
- Günde 1 kez 12 lig = 12 kredi/gün = 360 kredi/ay → bütçe içinde
"""

from __future__ import annotations

import datetime
import json
import os
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.the-odds-api.com/v4"

# iddaa bülteninde en çok görülen ligler; sıralama önemli değil
SOCCER_SPORT_KEYS = [
    "soccer_turkey_super_ligue",
    "soccer_turkey_1_lig",
    "soccer_epl",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_argentina_primera_division",
]

# Keskinlik sırası: Pinnacle en keskin, ardından exchange piyasalar
BOOKMAKER_PRIORITY = [
    "pinnacle",
    "betfair_ex_eu",
    "nordicbet",
    "marathonbet",
    "unibet_eu",
]

# Maç zamanı eşleşmesi için maksimum tolerans (saniye)
TIME_TOLERANCE_S = 3 * 3600  # 3 saat

# Takım adı benzerlik eşiği (0-1)
NAME_SIMILARITY_THRESHOLD = 0.72


@dataclass
class ExternalMatchOdds:
    odds_api_id: str
    sport_key: str
    home_team: str
    away_team: str
    commence_epoch: int | None
    bookmaker: str
    home_odd: float | None
    draw_odd: float | None
    away_odd: float | None
    # iddaa maç eşleşmesi
    iddaa_event_id: int | None = None
    match_confidence: float = 0.0


class OddsApiClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "ODDS_API_KEY bulunamadı. "
                "GitHub Secrets'a veya ortam değişkenine ekle."
            )
        self._remaining: int | None = None

    @property
    def credits_remaining(self) -> int | None:
        return self._remaining

    def _get(self, path: str, params: dict) -> dict | list:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE_URL}{path}?apiKey={self.api_key}&{qs}"
        req = Request(url, headers={"User-Agent": "iddaa-coupon/1.0"})
        try:
            with urlopen(req, timeout=15) as resp:
                self._remaining = int(resp.headers.get("x-requests-remaining", -1))
                return json.loads(resp.read())
        except HTTPError as exc:
            if exc.code == 401:
                raise RuntimeError("Odds API key geçersiz") from exc
            if exc.code == 422:
                return []   # sport_key mevcut değil
            raise

    def fetch_sport_odds(self, sport_key: str) -> list[dict]:
        """Bir lig için tüm yaklaşan maçların oranlarını çeker."""
        return self._get(
            f"/sports/{sport_key}/odds",
            {
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "unix",
            },
        )


# ── Team name normalisation ────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Takım adını karşılaştırma için normalleştirir."""
    name = name.lower()
    # Aksanları kaldır (Beşiktaş → besiktas)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    # Yaygın kulüp eklerini kaldır
    for suffix in [
        " fc", " cf", " sc", " fk", " sk", " bk", " afc", " utd",
        " united", " city", " town", " athletic", " atletico",
        " sporting", " sport", " club",
    ]:
        name = name.replace(suffix, "")
    return name.strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _best_bookmaker(bookmakers: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    """Öncelik listesine göre en keskin bookmaker'ı döner."""
    bk_map = {bk["key"]: bk for bk in bookmakers if bk.get("markets")}
    for key in BOOKMAKER_PRIORITY:
        if key in bk_map:
            return key, bk_map[key]
    # Öncelik listesinde yoksa ilkini al
    if bk_map:
        key = next(iter(bk_map))
        return key, bk_map[key]
    return None, None


def _extract_h2h(bookmaker: dict) -> tuple[float | None, float | None, float | None]:
    """1X2 oranlarını (home, draw, away) döner."""
    for market in bookmaker.get("markets", []):
        if market["key"] == "h2h":
            outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
            if len(outcomes) == 3:
                # 3-way: Draw ayrı bir outcome
                draw = outcomes.get("Draw")
                names = [k for k in outcomes if k != "Draw"]
                if len(names) == 2:
                    # Sıra: home, away bookmaker'a göre değişebilir
                    # Bookmaker "home_team" field'ı ile ilişkilendir
                    return outcomes.get(names[0]), draw, outcomes.get(names[1])
            elif len(outcomes) == 2:
                # 2-way (no draw) — nadiren h2h olur
                names = list(outcomes.keys())
                return outcomes[names[0]], None, outcomes[names[1]]
    return None, None, None


# ── Main matching function ─────────────────────────────────────────────────

def fetch_and_match(
    iddaa_events: list[dict],   # [{event_id, home_name, away_name, event_epoch}, ...]
    sport_keys: list[str] | None = None,
    api_key: str | None = None,
) -> list[ExternalMatchOdds]:
    """Tüm ligler için Odds API'den çeker ve iddaa maçlarıyla eşleştirir.

    Parameters
    ----------
    iddaa_events : list of dicts with keys event_id, home_name, away_name, event_epoch
    sport_keys   : çekilecek lig listesi; None ise SOCCER_SPORT_KEYS kullanılır
    api_key      : None ise ODDS_API_KEY env var'dan okunur
    """
    if sport_keys is None:
        sport_keys = SOCCER_SPORT_KEYS

    client = OddsApiClient(api_key=api_key)

    # iddaa maçlarını hızlı arama için index'e al
    iddaa_index = []
    for ev in iddaa_events:
        iddaa_index.append({
            "event_id": ev["event_id"],
            "home_norm": _normalize(ev["home_name"] or ""),
            "away_norm": _normalize(ev["away_name"] or ""),
            "epoch": ev["event_epoch"],
        })

    results: list[ExternalMatchOdds] = []

    for sport_key in sport_keys:
        try:
            api_events = client.fetch_sport_odds(sport_key)
        except Exception as exc:
            print(f"      [odds-api] {sport_key} hata: {exc}")
            continue

        for ae in api_events:
            commence = ae.get("commence_time")
            api_home = ae.get("home_team", "")
            api_away = ae.get("away_team", "")
            api_home_norm = _normalize(api_home)
            api_away_norm = _normalize(api_away)

            # iddaa maçıyla eşleştir
            best_conf = 0.0
            best_iddaa_id = None
            for ix in iddaa_index:
                # Zaman penceresi kontrolü
                if commence and ix["epoch"]:
                    if abs(commence - ix["epoch"]) > TIME_TOLERANCE_S:
                        continue
                sim_h = _similarity(api_home_norm, ix["home_norm"])
                sim_a = _similarity(api_away_norm, ix["away_norm"])
                conf = (sim_h + sim_a) / 2
                if conf > best_conf:
                    best_conf = conf
                    best_iddaa_id = ix["event_id"]

            if best_conf < NAME_SIMILARITY_THRESHOLD:
                best_iddaa_id = None  # eşleşme güvenilir değil

            # En keskin bookmaker'ı bul
            bk_key, bk = _best_bookmaker(ae.get("bookmakers", []))
            if bk is None:
                continue

            # Home/away sırasını düzelt: api home_team ile outcomes'ı eşleştir
            home_odd = draw_odd = away_odd = None
            for market in bk.get("markets", []):
                if market["key"] == "h2h":
                    outcome_map = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    home_odd = outcome_map.get(api_home)
                    away_odd = outcome_map.get(api_away)
                    draw_odd = outcome_map.get("Draw")
                    break

            results.append(ExternalMatchOdds(
                odds_api_id=ae["id"],
                sport_key=sport_key,
                home_team=api_home,
                away_team=api_away,
                commence_epoch=commence,
                bookmaker=bk_key,
                home_odd=home_odd,
                draw_odd=draw_odd,
                away_odd=away_odd,
                iddaa_event_id=best_iddaa_id,
                match_confidence=round(best_conf, 3),
            ))

        remaining = client.credits_remaining
        if remaining is not None and remaining < 20:
            print(f"      [odds-api] Uyarı: {remaining} kredi kaldı, çekim durduruluyor.")
            break

    matched = sum(1 for r in results if r.iddaa_event_id is not None)
    print(f"      [odds-api] {len(results)} maç çekildi, {matched} iddaa eşleşmesi")
    return results
