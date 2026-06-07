from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def ensure_parent_dir(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ingest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            sport_id INTEGER NOT NULL,
            bulletin_type INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            is_success INTEGER NOT NULL,
            version INTEGER,
            is_diff INTEGER,
            event_count INTEGER NOT NULL,
            raw_payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS competitions (
            id INTEGER PRIMARY KEY,
            sport_id INTEGER NOT NULL,
            country_code TEXT,
            name TEXT,
            short_name TEXT,
            icon_url TEXT,
            cref INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_snapshots (
            run_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            competition_id INTEGER,
            sport_id INTEGER NOT NULL,
            betradar_id INTEGER,
            version INTEGER,
            home_name TEXT,
            away_name TEXT,
            event_name TEXT,
            status INTEGER,
            betting_phase INTEGER,
            is_live INTEGER NOT NULL,
            mbc INTEGER,
            outcome_count INTEGER,
            event_epoch INTEGER,
            has_handicap INTEGER,
            king_odd INTEGER,
            king_mbc INTEGER,
            king_live INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
            run_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            market_type INTEGER,
            market_subtype INTEGER,
            version INTEGER,
            status INTEGER,
            mbc INTEGER,
            special_value TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, market_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS outcome_snapshots (
            run_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            outcome_no INTEGER NOT NULL,
            outcome_name TEXT,
            odd REAL,
            web_odd REAL,
            current_score TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, market_id, outcome_no),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS event_feature_snapshots (
            run_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            market_count INTEGER NOT NULL,
            outcome_count INTEGER NOT NULL,
            has_main_1x2 INTEGER NOT NULL,
            has_ou25 INTEGER NOT NULL,
            has_btts INTEGER NOT NULL,
            home_odd REAL,
            draw_odd REAL,
            away_odd REAL,
            implied_home REAL,
            implied_draw REAL,
            implied_away REAL,
            overround_1x2 REAL,
            favorite_side TEXT,
            favorite_odd REAL,
            favorite_gap_12 REAL,
            ou25_under_odd REAL,
            ou25_over_odd REAL,
            btts_yes_odd REAL,
            btts_no_odd REAL,
            goal_bias_score REAL,
            draw_pressure_score REAL,
            parity_score REAL,
            market_richness_score REAL NOT NULL,
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS score_snapshots (
            run_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            sport_id INTEGER NOT NULL,
            event_status INTEGER,
            match_status_code INTEGER,
            minute INTEGER,
            home_score INTEGER,
            away_score INTEGER,
            score_timestamp INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS event_result_labels (
            event_id INTEGER PRIMARY KEY,
            sport_id INTEGER NOT NULL,
            event_status INTEGER NOT NULL,
            home_score INTEGER NOT NULL,
            away_score INTEGER NOT NULL,
            total_goals INTEGER NOT NULL,
            result_1x2 TEXT NOT NULL,
            result_over_25 INTEGER NOT NULL,
            result_btts INTEGER NOT NULL,
            source_run_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS event_stats_snapshots (
            run_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            home_name TEXT,
            away_name TEXT,
            home_n_matches INTEGER NOT NULL DEFAULT 0,
            home_avg_scored REAL,
            home_avg_conceded REAL,
            home_wins INTEGER,
            home_draws INTEGER,
            home_losses INTEGER,
            away_n_matches INTEGER NOT NULL DEFAULT 0,
            away_avg_scored REAL,
            away_avg_conceded REAL,
            away_wins INTEGER,
            away_draws INTEGER,
            away_losses INTEGER,
            lambda_home REAL,
            lambda_away REAL,
            model_p1 REAL,
            model_px REAL,
            model_p2 REAL,
            model_p_over25 REAL,
            model_p_btts REAL,
            has_data INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS edge_scores (
            run_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            competition_id INTEGER,
            home_name TEXT,
            away_name TEXT,
            event_epoch INTEGER,
            overround_1x2 REAL,
            fair_home REAL,
            fair_draw REAL,
            fair_away REAL,
            fair_ou25_over REAL,
            fair_ou25_under REAL,
            fair_btts_yes REAL,
            fair_btts_no REAL,
            drift_home REAL,
            drift_draw REAL,
            drift_away REAL,
            best_leg_market TEXT,
            best_leg_outcome TEXT,
            best_leg_odd REAL,
            best_leg_fair_prob REAL,
            best_leg_drift REAL,
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS coupon_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            rank INTEGER NOT NULL,
            leg1_event_id INTEGER NOT NULL,
            leg1_home_name TEXT,
            leg1_away_name TEXT,
            leg1_competition_id INTEGER,
            leg1_outcome TEXT NOT NULL,
            leg1_odd REAL NOT NULL,
            leg1_fair_prob REAL NOT NULL,
            leg2_event_id INTEGER NOT NULL,
            leg2_home_name TEXT,
            leg2_away_name TEXT,
            leg2_competition_id INTEGER,
            leg2_outcome TEXT NOT NULL,
            leg2_odd REAL NOT NULL,
            leg2_fair_prob REAL NOT NULL,
            leg3_event_id INTEGER NOT NULL,
            leg3_home_name TEXT,
            leg3_away_name TEXT,
            leg3_competition_id INTEGER,
            leg3_outcome TEXT NOT NULL,
            leg3_odd REAL NOT NULL,
            leg3_fair_prob REAL NOT NULL,
            combined_odd REAL NOT NULL,
            combined_fair_prob REAL NOT NULL,
            expected_value REAL NOT NULL,
            final_score REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS external_odds_snapshots (
            odds_api_id TEXT NOT NULL,
            run_id INTEGER NOT NULL,
            sport_key TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            commence_epoch INTEGER,
            bookmaker TEXT NOT NULL,
            home_odd REAL,
            draw_odd REAL,
            away_odd REAL,
            iddaa_event_id INTEGER,
            match_confidence REAL,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (odds_api_id, run_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS coupon_results (
            event_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            odd REAL NOT NULL,
            fair_prob REAL NOT NULL,
            drift REAL NOT NULL DEFAULT 0,
            result TEXT,
            settled_at TEXT,
            PRIMARY KEY (event_id, run_id),
            FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS training_dataset_prematch (
            source_run_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            event_epoch INTEGER,
            competition_id INTEGER,
            home_name TEXT,
            away_name TEXT,
            market_count INTEGER NOT NULL,
            outcome_count INTEGER NOT NULL,
            has_main_1x2 INTEGER NOT NULL,
            has_ou25 INTEGER NOT NULL,
            has_btts INTEGER NOT NULL,
            home_odd REAL,
            draw_odd REAL,
            away_odd REAL,
            implied_home REAL,
            implied_draw REAL,
            implied_away REAL,
            overround_1x2 REAL,
            favorite_side TEXT,
            favorite_odd REAL,
            favorite_gap_12 REAL,
            ou25_under_odd REAL,
            ou25_over_odd REAL,
            btts_yes_odd REAL,
            btts_no_odd REAL,
            goal_bias_score REAL,
            draw_pressure_score REAL,
            parity_score REAL,
            market_richness_score REAL NOT NULL,
            label_home_score INTEGER NOT NULL,
            label_away_score INTEGER NOT NULL,
            label_total_goals INTEGER NOT NULL,
            label_result_1x2 TEXT NOT NULL,
            label_result_over_25 INTEGER NOT NULL,
            label_result_btts INTEGER NOT NULL,
            PRIMARY KEY (source_run_id, event_id),
            FOREIGN KEY (source_run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
        );
        """
    )
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that were introduced after the initial schema."""
    migrations = [
        "ALTER TABLE edge_scores ADD COLUMN drift_home REAL",
        "ALTER TABLE edge_scores ADD COLUMN drift_draw REAL",
        "ALTER TABLE edge_scores ADD COLUMN drift_away REAL",
        "ALTER TABLE edge_scores ADD COLUMN best_leg_drift REAL",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists


def insert_run(
    conn: sqlite3.Connection,
    *,
    source: str,
    sport_id: int,
    bulletin_type: int,
    endpoint: str,
    is_success: bool,
    version: int | None,
    is_diff: bool | None,
    event_count: int,
    raw_payload: dict,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO ingest_runs (
            source, sport_id, bulletin_type, endpoint, is_success,
            version, is_diff, event_count, raw_payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            sport_id,
            bulletin_type,
            endpoint,
            int(is_success),
            version,
            None if is_diff is None else int(is_diff),
            event_count,
            json.dumps(raw_payload, ensure_ascii=False),
        ),
    )
    return int(cursor.lastrowid)


def upsert_competitions(conn: sqlite3.Connection, competitions: list[dict]) -> None:
    rows = []
    for item in competitions:
        rows.append(
            (
                item.get("i"),
                int(item.get("si") or 0),
                item.get("cid"),
                item.get("n"),
                item.get("sn"),
                item.get("ic"),
                item.get("cref"),
                json.dumps(item, ensure_ascii=False),
            )
        )

    conn.executemany(
        """
        INSERT INTO competitions (
            id, sport_id, country_code, name, short_name, icon_url, cref, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            sport_id = excluded.sport_id,
            country_code = excluded.country_code,
            name = excluded.name,
            short_name = excluded.short_name,
            icon_url = excluded.icon_url,
            cref = excluded.cref,
            raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        rows,
    )


def insert_event_snapshot(conn: sqlite3.Connection, run_id: int, event: dict) -> None:
    conn.execute(
        """
        INSERT INTO event_snapshots (
            run_id, event_id, competition_id, sport_id, betradar_id, version,
            home_name, away_name, event_name, status, betting_phase, is_live,
            mbc, outcome_count, event_epoch, has_handicap, king_odd, king_mbc,
            king_live, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            event.get("i"),
            event.get("ci"),
            event.get("sid"),
            event.get("bri"),
            event.get("v"),
            event.get("hn"),
            event.get("an"),
            event.get("n"),
            event.get("s"),
            event.get("bp"),
            int(bool(event.get("il"))),
            event.get("mbc"),
            event.get("oc"),
            event.get("d"),
            int(bool(event.get("hc"))),
            int(bool(event.get("kOdd"))),
            int(bool(event.get("kMbc"))),
            int(bool(event.get("kLive"))),
            json.dumps(event, ensure_ascii=False),
        ),
    )


def insert_market_snapshot(
    conn: sqlite3.Connection, run_id: int, event_id: int, market: dict
) -> None:
    conn.execute(
        """
        INSERT INTO market_snapshots (
            run_id, market_id, event_id, market_type, market_subtype,
            version, status, mbc, special_value, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            market.get("i"),
            event_id,
            market.get("t"),
            market.get("st"),
            market.get("v"),
            market.get("s"),
            market.get("mbc"),
            market.get("sov"),
            json.dumps(market, ensure_ascii=False),
        ),
    )


def insert_outcome_snapshot(
    conn: sqlite3.Connection, run_id: int, event_id: int, market_id: int, outcome: dict
) -> None:
    conn.execute(
        """
        INSERT INTO outcome_snapshots (
            run_id, market_id, event_id, outcome_no, outcome_name,
            odd, web_odd, current_score, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            market_id,
            event_id,
            outcome.get("no"),
            outcome.get("n"),
            outcome.get("odd"),
            outcome.get("wodd"),
            outcome.get("cs"),
            json.dumps(outcome, ensure_ascii=False),
        ),
    )


def insert_event_feature_snapshot(conn: sqlite3.Connection, run_id: int, features) -> None:
    conn.execute(
        """
        INSERT INTO event_feature_snapshots (
            run_id, event_id, market_count, outcome_count, has_main_1x2,
            has_ou25, has_btts, home_odd, draw_odd, away_odd,
            implied_home, implied_draw, implied_away, overround_1x2,
            favorite_side, favorite_odd, favorite_gap_12,
            ou25_under_odd, ou25_over_odd, btts_yes_odd, btts_no_odd,
            goal_bias_score, draw_pressure_score, parity_score, market_richness_score
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            features.event_id,
            features.market_count,
            features.outcome_count,
            int(features.has_main_1x2),
            int(features.has_ou25),
            int(features.has_btts),
            features.home_odd,
            features.draw_odd,
            features.away_odd,
            features.implied_home,
            features.implied_draw,
            features.implied_away,
            features.overround_1x2,
            features.favorite_side,
            features.favorite_odd,
            features.favorite_gap_12,
            features.ou25_under_odd,
            features.ou25_over_odd,
            features.btts_yes_odd,
            features.btts_no_odd,
            features.goal_bias_score,
            features.draw_pressure_score,
            features.parity_score,
            features.market_richness_score,
        ),
    )


def upsert_event_stats(conn: sqlite3.Connection, run_id: int, snap) -> None:
    """snap: EventStatsSnapshot dataclass or similar with all fields."""
    conn.execute(
        """
        INSERT INTO event_stats_snapshots (
            run_id, event_id, home_name, away_name,
            home_n_matches, home_avg_scored, home_avg_conceded,
            home_wins, home_draws, home_losses,
            away_n_matches, away_avg_scored, away_avg_conceded,
            away_wins, away_draws, away_losses,
            lambda_home, lambda_away,
            model_p1, model_px, model_p2, model_p_over25, model_p_btts,
            has_data
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, event_id) DO UPDATE SET
            home_avg_scored = excluded.home_avg_scored,
            home_avg_conceded = excluded.home_avg_conceded,
            away_avg_scored = excluded.away_avg_scored,
            away_avg_conceded = excluded.away_avg_conceded,
            lambda_home = excluded.lambda_home,
            lambda_away = excluded.lambda_away,
            model_p1 = excluded.model_p1,
            model_px = excluded.model_px,
            model_p2 = excluded.model_p2,
            model_p_over25 = excluded.model_p_over25,
            model_p_btts = excluded.model_p_btts,
            has_data = excluded.has_data
        """,
        (
            run_id, snap.event_id, snap.home_name, snap.away_name,
            snap.home_n_matches, snap.home_avg_scored, snap.home_avg_conceded,
            snap.home_wins, snap.home_draws, snap.home_losses,
            snap.away_n_matches, snap.away_avg_scored, snap.away_avg_conceded,
            snap.away_wins, snap.away_draws, snap.away_losses,
            snap.lambda_home, snap.lambda_away,
            snap.model_p1, snap.model_px, snap.model_p2,
            snap.model_p_over25, snap.model_p_btts,
            int(snap.has_data),
        ),
    )


def insert_edge_score(conn: sqlite3.Connection, edge) -> None:
    best = edge.best_leg
    conn.execute(
        """
        INSERT INTO edge_scores (
            run_id, event_id, competition_id, home_name, away_name, event_epoch,
            overround_1x2, fair_home, fair_draw, fair_away,
            fair_ou25_over, fair_ou25_under, fair_btts_yes, fair_btts_no,
            drift_home, drift_draw, drift_away,
            best_leg_market, best_leg_outcome, best_leg_odd, best_leg_fair_prob, best_leg_drift
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, event_id) DO UPDATE SET
            overround_1x2 = excluded.overround_1x2,
            fair_home = excluded.fair_home,
            fair_draw = excluded.fair_draw,
            fair_away = excluded.fair_away,
            fair_ou25_over = excluded.fair_ou25_over,
            fair_ou25_under = excluded.fair_ou25_under,
            fair_btts_yes = excluded.fair_btts_yes,
            fair_btts_no = excluded.fair_btts_no,
            drift_home = excluded.drift_home,
            drift_draw = excluded.drift_draw,
            drift_away = excluded.drift_away,
            best_leg_market = excluded.best_leg_market,
            best_leg_outcome = excluded.best_leg_outcome,
            best_leg_odd = excluded.best_leg_odd,
            best_leg_fair_prob = excluded.best_leg_fair_prob,
            best_leg_drift = excluded.best_leg_drift
        """,
        (
            edge.run_id,
            edge.event_id,
            edge.competition_id,
            edge.home_name,
            edge.away_name,
            edge.event_epoch,
            edge.overround_1x2,
            edge.fair_home,
            edge.fair_draw,
            edge.fair_away,
            edge.fair_ou25_over,
            edge.fair_ou25_under,
            edge.fair_btts_yes,
            edge.fair_btts_no,
            edge.drift_home,
            edge.drift_draw,
            edge.drift_away,
            best.market if best else None,
            best.outcome if best else None,
            best.odd if best else None,
            best.fair_prob if best else None,
            best.drift if best else None,
        ),
    )


def save_coupon_candidates(conn: sqlite3.Connection, run_id: int, coupons) -> None:
    conn.execute("DELETE FROM coupon_candidates WHERE run_id = ?", (run_id,))
    rows = []
    for rank, coupon in enumerate(coupons, 1):
        l1, l2, l3 = coupon.legs
        rows.append((
            run_id, rank,
            l1.event_id, l1.home_name, l1.away_name, l1.competition_id, l1.outcome, l1.odd, l1.fair_prob,
            l2.event_id, l2.home_name, l2.away_name, l2.competition_id, l2.outcome, l2.odd, l2.fair_prob,
            l3.event_id, l3.home_name, l3.away_name, l3.competition_id, l3.outcome, l3.odd, l3.fair_prob,
            coupon.combined_odd,
            coupon.combined_fair_prob,
            coupon.expected_value,
            coupon.final_score,
        ))
    conn.executemany(
        """
        INSERT INTO coupon_candidates (
            run_id, rank,
            leg1_event_id, leg1_home_name, leg1_away_name, leg1_competition_id,
            leg1_outcome, leg1_odd, leg1_fair_prob,
            leg2_event_id, leg2_home_name, leg2_away_name, leg2_competition_id,
            leg2_outcome, leg2_odd, leg2_fair_prob,
            leg3_event_id, leg3_home_name, leg3_away_name, leg3_competition_id,
            leg3_outcome, leg3_odd, leg3_fair_prob,
            combined_odd, combined_fair_prob, expected_value, final_score
        )
        VALUES (
            ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        """,
        rows,
    )


def insert_external_odds(conn: sqlite3.Connection, run_id: int, records: list) -> None:
    """Odds API'den çekilen dış bahisçi oranlarını kaydet."""
    rows = [
        (
            r.odds_api_id, run_id, r.sport_key,
            r.home_team, r.away_team, r.commence_epoch,
            r.bookmaker, r.home_odd, r.draw_odd, r.away_odd,
            r.iddaa_event_id, r.match_confidence,
        )
        for r in records
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO external_odds_snapshots (
            odds_api_id, run_id, sport_key,
            home_team, away_team, commence_epoch,
            bookmaker, home_odd, draw_odd, away_odd,
            iddaa_event_id, match_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_score_snapshot(conn: sqlite3.Connection, run_id: int, event: dict) -> None:
    score = event.get("sc") or {}
    home = score.get("ht") or {}
    away = score.get("at") or {}
    conn.execute(
        """
        INSERT INTO score_snapshots (
            run_id, event_id, sport_id, event_status, match_status_code,
            minute, home_score, away_score, score_timestamp, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            event.get("i"),
            event.get("sid"),
            event.get("s"),
            score.get("s"),
            score.get("min"),
            home.get("c"),
            away.get("c"),
            score.get("t"),
            json.dumps(event, ensure_ascii=False),
        ),
    )
