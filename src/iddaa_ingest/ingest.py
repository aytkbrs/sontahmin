from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .client import IddaaClient
from .config import (
    BASE_URL,
    BULLETIN_TYPE_PREMATCH,
    BULLETIN_TYPE_LIVE,
    BULLETIN_TYPE_LONGTERM,
    DB_PATH,
    SPORT_ID_FOOTBALL,
    football_bulletin_path,
)
from .db import (
    connect,
    init_db,
    insert_event_snapshot,
    insert_event_feature_snapshot,
    insert_market_snapshot,
    insert_outcome_snapshot,
    insert_score_snapshot,
    insert_run,
    upsert_competitions,
)
from .features import build_event_features


@dataclass(slots=True)
class IngestResult:
    run_id: int
    bulletin_type: int
    event_count: int
    market_count: int
    outcome_count: int
    feature_count: int
    score_count: int
    db_path: str
    changed: bool = True   # False when bulletin version matched last run → nothing written


def _persist_events(conn: sqlite3.Connection, run_id: int, events: list[dict]) -> tuple[int, int, int]:
    market_count = 0
    outcome_count = 0
    feature_count = 0

    for event in events:
        event_id = event["i"]
        insert_event_snapshot(conn, run_id, event)
        insert_event_feature_snapshot(conn, run_id, build_event_features(event))
        feature_count += 1

        for market in event.get("m", []):
            market_count += 1
            market_id = market["i"]
            insert_market_snapshot(conn, run_id, event_id, market)

            for outcome in market.get("o", []):
                outcome_count += 1
                insert_outcome_snapshot(conn, run_id, event_id, market_id, outcome)

    return market_count, outcome_count, feature_count


def ingest_football_bulletin(bulletin_type: int, db_path=DB_PATH) -> IngestResult:
    client = IddaaClient()
    competitions_payload = client.fetch_competitions()
    bulletin_payload = client.fetch_football_bulletin(bulletin_type)

    events = bulletin_payload.get("data", {}).get("events", [])
    version = bulletin_payload.get("data", {}).get("version")
    is_diff = bulletin_payload.get("data", {}).get("isdiff")
    endpoint = football_bulletin_path(bulletin_type)

    with connect(db_path) as conn:
        init_db(conn)

        # Diff check: if the bulletin version hasn't changed, skip writing a new run.
        # iddaa updates `version` (a ms-timestamp) whenever any odds change.
        if version is not None:
            last = conn.execute(
                "SELECT id, version, event_count FROM ingest_runs "
                "WHERE bulletin_type = ? ORDER BY id DESC LIMIT 1",
                (bulletin_type,),
            ).fetchone()
            if last and last["version"] == version:
                return IngestResult(
                    run_id=last["id"],
                    bulletin_type=bulletin_type,
                    event_count=last["event_count"],
                    market_count=0,
                    outcome_count=0,
                    feature_count=0,
                    score_count=0,
                    db_path=str(db_path),
                    changed=False,
                )

        upsert_competitions(conn, competitions_payload.get("data", []))

        run_id = insert_run(
            conn,
            source=BASE_URL,
            sport_id=SPORT_ID_FOOTBALL,
            bulletin_type=bulletin_type,
            endpoint=endpoint,
            is_success=bool(bulletin_payload.get("isSuccess")),
            version=version,
            is_diff=is_diff,
            event_count=len(events),
            raw_payload=bulletin_payload,
        )

        market_count, outcome_count, feature_count = _persist_events(conn, run_id, events)

    return IngestResult(
        run_id=run_id,
        bulletin_type=bulletin_type,
        event_count=len(events),
        market_count=market_count,
        outcome_count=outcome_count,
        feature_count=feature_count,
        score_count=0,
        db_path=str(db_path),
        changed=True,
    )


def ingest_live_football(db_path=DB_PATH) -> IngestResult:
    return ingest_football_bulletin(BULLETIN_TYPE_LIVE, db_path=db_path)


def ingest_prematch_football(db_path=DB_PATH) -> IngestResult:
    return ingest_football_bulletin(BULLETIN_TYPE_PREMATCH, db_path=db_path)


def ingest_live_scoreboard(db_path=DB_PATH) -> IngestResult:
    client = IddaaClient()
    payload = client.fetch_live_score_widget()
    events = [
        event
        for event in payload.get("data", [])
        if event.get("sid") == SPORT_ID_FOOTBALL
    ]

    with connect(db_path) as conn:
        init_db(conn)
        run_id = insert_run(
            conn,
            source=BASE_URL,
            sport_id=SPORT_ID_FOOTBALL,
            bulletin_type=BULLETIN_TYPE_LIVE,
            endpoint="/sportsbook/live-events-for-widget",
            is_success=bool(payload.get("isSuccess")),
            version=None,
            is_diff=None,
            event_count=len(events),
            raw_payload=payload,
        )
        for event in events:
            insert_score_snapshot(conn, run_id, event)

    return IngestResult(
        run_id=run_id,
        bulletin_type=BULLETIN_TYPE_LIVE,
        event_count=len(events),
        market_count=0,
        outcome_count=0,
        feature_count=0,
        score_count=len(events),
        db_path=str(db_path),
        changed=True,
    )
