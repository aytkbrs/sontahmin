from __future__ import annotations

import sqlite3

from .config import EVENT_STATUS_CLOSED, EVENT_STATUS_ENDED, SPORT_ID_FOOTBALL


FINISHED_EVENT_STATUSES = {EVENT_STATUS_ENDED, EVENT_STATUS_CLOSED}


def generate_result_labels(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT
            ss.run_id,
            ss.event_id,
            ss.sport_id,
            ss.event_status,
            ss.home_score,
            ss.away_score
        FROM score_snapshots ss
        JOIN (
            SELECT event_id, MAX(run_id) AS max_run_id
            FROM score_snapshots
            WHERE sport_id = ?
              AND event_status IN (?, ?)
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            GROUP BY event_id
        ) latest
          ON latest.event_id = ss.event_id
         AND latest.max_run_id = ss.run_id
        """,
        (SPORT_ID_FOOTBALL, EVENT_STATUS_ENDED, EVENT_STATUS_CLOSED),
    ).fetchall()

    payload = []
    for row in rows:
        home_score = int(row["home_score"])
        away_score = int(row["away_score"])
        total_goals = home_score + away_score
        if home_score > away_score:
            result_1x2 = "1"
        elif home_score < away_score:
            result_1x2 = "2"
        else:
            result_1x2 = "0"

        payload.append(
            (
                row["event_id"],
                row["sport_id"],
                row["event_status"],
                home_score,
                away_score,
                total_goals,
                result_1x2,
                int(total_goals > 2),
                int(home_score > 0 and away_score > 0),
                row["run_id"],
            )
        )

    conn.executemany(
        """
        INSERT INTO event_result_labels (
            event_id, sport_id, event_status, home_score, away_score, total_goals,
            result_1x2, result_over_25, result_btts, source_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            sport_id = excluded.sport_id,
            event_status = excluded.event_status,
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            total_goals = excluded.total_goals,
            result_1x2 = excluded.result_1x2,
            result_over_25 = excluded.result_over_25,
            result_btts = excluded.result_btts,
            source_run_id = excluded.source_run_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        payload,
    )
    return len(payload)


def build_prematch_training_dataset(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM training_dataset_prematch")
    conn.execute(
        """
        INSERT INTO training_dataset_prematch (
            source_run_id, event_id, event_epoch, competition_id, home_name, away_name,
            market_count, outcome_count, has_main_1x2, has_ou25, has_btts,
            home_odd, draw_odd, away_odd, implied_home, implied_draw, implied_away,
            overround_1x2, favorite_side, favorite_odd, favorite_gap_12,
            ou25_under_odd, ou25_over_odd, btts_yes_odd, btts_no_odd,
            goal_bias_score, draw_pressure_score, parity_score, market_richness_score,
            label_home_score, label_away_score, label_total_goals,
            label_result_1x2, label_result_over_25, label_result_btts
        )
        SELECT
            es.run_id,
            es.event_id,
            es.event_epoch,
            es.competition_id,
            es.home_name,
            es.away_name,
            fs.market_count,
            fs.outcome_count,
            fs.has_main_1x2,
            fs.has_ou25,
            fs.has_btts,
            fs.home_odd,
            fs.draw_odd,
            fs.away_odd,
            fs.implied_home,
            fs.implied_draw,
            fs.implied_away,
            fs.overround_1x2,
            fs.favorite_side,
            fs.favorite_odd,
            fs.favorite_gap_12,
            fs.ou25_under_odd,
            fs.ou25_over_odd,
            fs.btts_yes_odd,
            fs.btts_no_odd,
            fs.goal_bias_score,
            fs.draw_pressure_score,
            fs.parity_score,
            fs.market_richness_score,
            rl.home_score,
            rl.away_score,
            rl.total_goals,
            rl.result_1x2,
            rl.result_over_25,
            rl.result_btts
        FROM event_feature_snapshots fs
        JOIN event_snapshots es
          ON es.run_id = fs.run_id
         AND es.event_id = fs.event_id
        JOIN event_result_labels rl
          ON rl.event_id = fs.event_id
        JOIN (
            SELECT event_id, MAX(run_id) AS max_run_id
            FROM event_snapshots
            WHERE sport_id = ?
              AND event_name IS NULL
              AND home_name IS NOT NULL
              AND away_name IS NOT NULL
            GROUP BY event_id
        ) latest
          ON latest.event_id = es.event_id
         AND latest.max_run_id = es.run_id
        WHERE es.sport_id = ?
        """,
        (SPORT_ID_FOOTBALL, SPORT_ID_FOOTBALL),
    )
    return conn.execute("SELECT COUNT(*) FROM training_dataset_prematch").fetchone()[0]
