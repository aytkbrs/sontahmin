"""Takım profil sistemi — maç sonuçlarından birikim.

Her maç etiketlendiğinde (generate-labels) her iki takımın ev/deplasman
istatistikleri bu modüle kaydedilir.  Poisson modeli yeterli geçmiş
varsa (≥MIN_MATCHES) API'nin 6 maçlık penceresini kendi birikmiş
verimizle değiştirir.

Kazanım:
  - 6 maç  → yüksek varyans
  - 30 maç → orta güven
  - 80 maç → kararlı tahmin, ligin gerçek güç dengesini yansıtır
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass

# Kaç maç varsa kendi verimizi kullanmaya başlayalım?
MIN_MATCHES_TO_USE = 8

# Bayesian blending: kendi verimiz ne kadar güvenilirken API'yi ne kadar kullanalım
# n_ours büyüdükçe API ağırlığı otomatik düşer (n_ours / (n_ours + 6))
_API_WEIGHT = 6   # API verisini kaç fantom maç olarak sayıyoruz


@dataclass
class TeamProfile:
    team_id: int
    name: str
    n_home: int
    n_away: int
    home_scored: float      # ortalama ev golü
    home_conceded: float    # ortalama ev yenilen
    away_scored: float
    away_conceded: float
    home_win_rate: float
    away_win_rate: float


def _normalize_name(name: str) -> str:
    """Karşılaştırma için takım adını normalleştirir."""
    name = name.lower().strip()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return name


# ── Schema ─────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS teams (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE    -- normalleştirilmiş isim
);

CREATE TABLE IF NOT EXISTS team_match_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER NOT NULL,
    opponent_id    INTEGER NOT NULL,
    event_id       INTEGER NOT NULL,
    is_home        INTEGER NOT NULL,   -- 1=ev, 0=deplasman
    goals_scored   INTEGER NOT NULL,
    goals_conceded INTEGER NOT NULL,
    result         TEXT NOT NULL,      -- 'win'|'draw'|'loss'
    match_epoch    INTEGER,
    competition_id INTEGER,
    source_run_id  INTEGER NOT NULL,
    UNIQUE (team_id, event_id),
    FOREIGN KEY (team_id)     REFERENCES teams(id),
    FOREIGN KEY (opponent_id) REFERENCES teams(id)
);
"""


def init_team_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Migration: bazı eski DB'lerde tablo olmayabilir, güvenli
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_tmr_team ON team_match_results(team_id)",
        "CREATE INDEX IF NOT EXISTS idx_tmr_event ON team_match_results(event_id)",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


# ── Team upsert ─────────────────────────────────────────────────────────────

def _get_or_create_team(conn: sqlite3.Connection, raw_name: str) -> int:
    norm = _normalize_name(raw_name)
    row = conn.execute("SELECT id FROM teams WHERE name=?", (norm,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO teams (name) VALUES (?)", (norm,))
    return cur.lastrowid


# ── Populate from labels ────────────────────────────────────────────────────

def update_profiles_from_labels(conn: sqlite3.Connection) -> int:
    """Etiketlenmiş maçları team_match_results'a işle.

    Daha önce işlenmiş maçları atlar (UNIQUE constraint).
    Returns: eklenen yeni satır sayısı
    """
    init_team_schema(conn)

    labeled = conn.execute(
        """
        SELECT
            lbl.event_id,
            lbl.home_score,
            lbl.away_score,
            lbl.result_1x2,
            lbl.source_run_id,
            es.home_name,
            es.away_name,
            es.event_epoch,
            es.competition_id
        FROM event_result_labels lbl
        JOIN event_snapshots es
          ON es.event_id = lbl.event_id
        WHERE lbl.event_id NOT IN (
            SELECT DISTINCT event_id FROM team_match_results
        )
        ORDER BY lbl.event_id
        """
    ).fetchall()

    added = 0
    for row in labeled:
        home_name = row["home_name"]
        away_name = row["away_name"]
        if not home_name or not away_name:
            continue

        home_id = _get_or_create_team(conn, home_name)
        away_id = _get_or_create_team(conn, away_name)

        result_1x2 = row["result_1x2"]  # 'home' | 'draw' | 'away'
        hs = row["home_score"]
        as_ = row["away_score"]

        # Ev sahibi satırı
        try:
            conn.execute(
                """
                INSERT INTO team_match_results
                (team_id, opponent_id, event_id, is_home,
                 goals_scored, goals_conceded, result,
                 match_epoch, competition_id, source_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    home_id, away_id, row["event_id"], 1,
                    hs, as_,
                    "win" if result_1x2 == "home" else ("draw" if result_1x2 == "draw" else "loss"),
                    row["event_epoch"], row["competition_id"], row["source_run_id"],
                ),
            )
            added += 1
        except sqlite3.IntegrityError:
            pass  # zaten var

        # Deplasman satırı
        try:
            conn.execute(
                """
                INSERT INTO team_match_results
                (team_id, opponent_id, event_id, is_home,
                 goals_scored, goals_conceded, result,
                 match_epoch, competition_id, source_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    away_id, home_id, row["event_id"], 0,
                    as_, hs,
                    "win" if result_1x2 == "away" else ("draw" if result_1x2 == "draw" else "loss"),
                    row["event_epoch"], row["competition_id"], row["source_run_id"],
                ),
            )
            added += 1
        except sqlite3.IntegrityError:
            pass

    return added // 2  # maç sayısı döner


# ── Profile query ───────────────────────────────────────────────────────────

def get_team_profile(conn: sqlite3.Connection, raw_name: str) -> TeamProfile | None:
    """Bir takımın birikmiş profilini döner; yeterli veri yoksa None."""
    init_team_schema(conn)
    norm = _normalize_name(raw_name)
    team = conn.execute("SELECT id FROM teams WHERE name=?", (norm,)).fetchone()
    if team is None:
        return None
    tid = team[0]

    stats = conn.execute(
        """
        SELECT
            is_home,
            COUNT(*)                           AS n,
            AVG(goals_scored)                  AS avg_scored,
            AVG(goals_conceded)                AS avg_conceded,
            SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
        FROM team_match_results
        WHERE team_id = ?
        GROUP BY is_home
        """,
        (tid,),
    ).fetchall()

    home_row = next((s for s in stats if s["is_home"] == 1), None)
    away_row = next((s for s in stats if s["is_home"] == 0), None)

    n_home = home_row["n"] if home_row else 0
    n_away = away_row["n"] if away_row else 0

    if n_home < MIN_MATCHES_TO_USE and n_away < MIN_MATCHES_TO_USE:
        return None  # yeterli veri yok

    return TeamProfile(
        team_id=tid,
        name=norm,
        n_home=n_home,
        n_away=n_away,
        home_scored=home_row["avg_scored"] if home_row else 1.4,
        home_conceded=home_row["avg_conceded"] if home_row else 1.1,
        away_scored=away_row["avg_scored"] if away_row else 1.1,
        away_conceded=away_row["avg_conceded"] if away_row else 1.4,
        home_win_rate=home_row["win_rate"] if home_row else 0.45,
        away_win_rate=away_row["win_rate"] if away_row else 0.30,
    )


# ── Blending API + profile ──────────────────────────────────────────────────

def blend_api_and_profile(
    api_avg: float,
    api_n: int,
    profile_avg: float,
    profile_n: int,
) -> float:
    """API verisini (son N maç) kendi profilimizle Bayesian blend yap.

    API verisi _API_WEIGHT (=6) fantom maç olarak sayılır.
    Profile N arttıkça ağırlığı yükselir, API ağırlığı azalır.
    """
    # Önce ikisini birleştirip toplam ağırlığa göre ağırlıklı ortalama al
    weight_api = _API_WEIGHT
    weight_profile = profile_n
    return (weight_api * api_avg + weight_profile * profile_avg) / (weight_api + weight_profile)
