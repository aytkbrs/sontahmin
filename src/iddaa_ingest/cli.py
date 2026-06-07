from __future__ import annotations

import argparse
import io
import sys

# Ensure UTF-8 output on Windows terminals that default to a legacy code page.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from .config import BULLETIN_TYPE_LIVE, BULLETIN_TYPE_LONGTERM, BULLETIN_TYPE_PREMATCH, DB_PATH
import time

from .coupon import build_coupons, format_coupon
from .db import connect, init_db, insert_edge_score, save_coupon_candidates, upsert_event_stats
from .ingest import (
    ingest_football_bulletin,
    ingest_live_football,
    ingest_live_scoreboard,
    ingest_prematch_football,
)
from .labels import build_prematch_training_dataset, generate_result_labels
from .model import score_latest_prematch
from .poisson import compute_match_probs
from .stats import extract_match_stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="iddaa ingest CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "fetch-live-football",
        help="Fetch the live football bulletin and persist it to SQLite",
    )
    subparsers.add_parser(
        "fetch-prematch-football",
        help="Fetch the pre-match football bulletin and persist it to SQLite",
    )
    subparsers.add_parser(
        "fetch-live-scores",
        help="Fetch live football scoreboard snapshots and persist them to SQLite",
    )
    subparsers.add_parser(
        "generate-labels",
        help="Generate settled match labels from stored score snapshots",
    )
    subparsers.add_parser(
        "build-training-dataset",
        help="Build the pre-match training dataset from latest feature snapshots and labels",
    )
    fetch_stats_p = subparsers.add_parser(
        "fetch-stats",
        help="Fetch form statistics for all events in the latest prematch run",
    )
    fetch_stats_p.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Seconds to wait between API calls (default: 0.3)",
    )

    subparsers.add_parser(
        "score-edges",
        help="Compute fair probabilities and edge scores for the latest prematch run",
    )
    build_cp = subparsers.add_parser(
        "build-coupons",
        help="Build and display top coupon candidates from the latest edge scores",
    )
    build_cp.add_argument(
        "--top",
        type=int,
        default=5,
        help="Number of coupons to display (default: 5)",
    )
    build_cp.add_argument(
        "--min-odd",
        type=float,
        default=2.50,
        help="Minimum combined odd for a coupon (default: 2.50)",
    )
    build_cp.add_argument(
        "--today",
        action="store_true",
        help="Only include matches happening today (Turkey time)",
    )
    build_cp.add_argument(
        "--date",
        type=str,
        default=None,
        help="Only include matches on this date, YYYY-MM-DD (Turkey time)",
    )

    generic = subparsers.add_parser(
        "fetch-football",
        help="Fetch football bulletin by type and persist it to SQLite",
    )
    generic.add_argument(
        "--type",
        type=int,
        choices=[BULLETIN_TYPE_PREMATCH, BULLETIN_TYPE_LIVE, BULLETIN_TYPE_LONGTERM],
        required=True,
        help="0=prematch, 1=live, 2=longterm",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch-live-football":
        result = ingest_live_football()
        print(
            "type={bulletin_type} run_id={run_id} events={events} markets={markets} outcomes={outcomes} features={features} scores={scores} db={db}".format(
                run_id=result.run_id,
                bulletin_type=result.bulletin_type,
                events=result.event_count,
                markets=result.market_count,
                outcomes=result.outcome_count,
                features=result.feature_count,
                scores=result.score_count,
                db=result.db_path,
            )
        )
        return 0

    if args.command == "fetch-prematch-football":
        result = ingest_prematch_football()
        print(
            "type={bulletin_type} run_id={run_id} events={events} markets={markets} outcomes={outcomes} features={features} scores={scores} db={db}".format(
                run_id=result.run_id,
                bulletin_type=result.bulletin_type,
                events=result.event_count,
                markets=result.market_count,
                outcomes=result.outcome_count,
                features=result.feature_count,
                scores=result.score_count,
                db=result.db_path,
            )
        )
        return 0

    if args.command == "fetch-live-scores":
        result = ingest_live_scoreboard()
        print(
            "type={bulletin_type} run_id={run_id} events={events} markets={markets} outcomes={outcomes} features={features} scores={scores} db={db}".format(
                run_id=result.run_id,
                bulletin_type=result.bulletin_type,
                events=result.event_count,
                markets=result.market_count,
                outcomes=result.outcome_count,
                features=result.feature_count,
                scores=result.score_count,
                db=result.db_path,
            )
        )
        return 0

    if args.command == "fetch-football":
        result = ingest_football_bulletin(args.type)
        print(
            "type={bulletin_type} run_id={run_id} events={events} markets={markets} outcomes={outcomes} features={features} scores={scores} db={db}".format(
                run_id=result.run_id,
                bulletin_type=result.bulletin_type,
                events=result.event_count,
                markets=result.market_count,
                outcomes=result.outcome_count,
                features=result.feature_count,
                scores=result.score_count,
                db=result.db_path,
            )
        )
        return 0

    if args.command == "generate-labels":
        with connect(DB_PATH) as conn:
            init_db(conn)
            count = generate_result_labels(conn)
        print(f"generated_labels={count}")
        return 0

    if args.command == "build-training-dataset":
        with connect(DB_PATH) as conn:
            init_db(conn)
            count = build_prematch_training_dataset(conn)
        print(f"training_rows={count}")
        return 0

    if args.command == "fetch-stats":
        from .client import StatsClient
        from dataclasses import dataclass

        @dataclass
        class StatsSnap:
            event_id: int
            home_name: str | None
            away_name: str | None
            home_n_matches: int
            home_avg_scored: float | None
            home_avg_conceded: float | None
            home_wins: int | None
            home_draws: int | None
            home_losses: int | None
            away_n_matches: int
            away_avg_scored: float | None
            away_avg_conceded: float | None
            away_wins: int | None
            away_draws: int | None
            away_losses: int | None
            lambda_home: float | None
            lambda_away: float | None
            model_p1: float | None
            model_px: float | None
            model_p2: float | None
            model_p_over25: float | None
            model_p_btts: float | None
            has_data: bool

        stats_client = StatsClient()
        with connect(DB_PATH) as conn:
            init_db(conn)
            run = conn.execute(
                "SELECT id FROM ingest_runs WHERE bulletin_type = 0 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if run is None:
                print("Prematch bülteni bulunamadı.")
                return 1
            run_id = run["id"]
            event_rows = conn.execute(
                """
                SELECT es.event_id, es.home_name, es.away_name
                FROM event_snapshots es
                WHERE es.run_id = ?
                ORDER BY es.event_id
                """,
                (run_id,),
            ).fetchall()

        total = len(event_rows)
        ok = 0
        errors = 0
        snaps = []

        for i, ev in enumerate(event_rows):
            event_id = ev["event_id"]
            home_name = ev["home_name"]
            away_name = ev["away_name"]
            try:
                r = stats_client.fetch_card_corners(event_id)
                if not r.get("isSuccess"):
                    raise RuntimeError("isSuccess=false")
                ms = extract_match_stats(event_id, r.get("data", {}))
                probs = compute_match_probs(ms) if ms.has_data else None
                snap = StatsSnap(
                    event_id=event_id,
                    home_name=home_name,
                    away_name=away_name,
                    home_n_matches=ms.home.n_matches,
                    home_avg_scored=ms.home.avg_scored if ms.has_data else None,
                    home_avg_conceded=ms.home.avg_conceded if ms.has_data else None,
                    home_wins=ms.home.wins if ms.has_data else None,
                    home_draws=ms.home.draws if ms.has_data else None,
                    home_losses=ms.home.losses if ms.has_data else None,
                    away_n_matches=ms.away.n_matches,
                    away_avg_scored=ms.away.avg_scored if ms.has_data else None,
                    away_avg_conceded=ms.away.avg_conceded if ms.has_data else None,
                    away_wins=ms.away.wins if ms.has_data else None,
                    away_draws=ms.away.draws if ms.has_data else None,
                    away_losses=ms.away.losses if ms.has_data else None,
                    lambda_home=probs.lambda_home if probs else None,
                    lambda_away=probs.lambda_away if probs else None,
                    model_p1=probs.p1 if probs else None,
                    model_px=probs.px if probs else None,
                    model_p2=probs.p2 if probs else None,
                    model_p_over25=probs.p_over25 if probs else None,
                    model_p_btts=probs.p_btts if probs else None,
                    has_data=ms.has_data,
                )
                snaps.append(snap)
                ok += 1
            except Exception:
                snap = StatsSnap(
                    event_id=event_id, home_name=home_name, away_name=away_name,
                    home_n_matches=0, home_avg_scored=None, home_avg_conceded=None,
                    home_wins=None, home_draws=None, home_losses=None,
                    away_n_matches=0, away_avg_scored=None, away_avg_conceded=None,
                    away_wins=None, away_draws=None, away_losses=None,
                    lambda_home=None, lambda_away=None,
                    model_p1=None, model_px=None, model_p2=None,
                    model_p_over25=None, model_p_btts=None,
                    has_data=False,
                )
                snaps.append(snap)
                errors += 1

            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{total} işlendi...")
            time.sleep(args.delay)

        with connect(DB_PATH) as conn:
            init_db(conn)
            for snap in snaps:
                upsert_event_stats(conn, run_id, snap)

        with_data = sum(1 for s in snaps if s.has_data)
        print(f"run_id={run_id} total={total} with_stats={with_data} errors={errors}")
        return 0

    if args.command == "score-edges":
        with connect(DB_PATH) as conn:
            init_db(conn)
            run_id, edges = score_latest_prematch(conn)
            if run_id == 0:
                print("Prematch bülteni bulunamadı. Önce fetch-prematch-football çalıştırın.")
                return 1
            for edge in edges:
                insert_edge_score(conn, edge)
        scored = sum(1 for e in edges if e.best_leg is not None)
        print(
            f"run_id={run_id} events={len(edges)} scored={scored}"
        )
        return 0

    if args.command == "build-coupons":
        import datetime as _dt
        date_filter = None
        if args.today:
            tz = _dt.timezone(_dt.timedelta(hours=3))
            date_filter = _dt.datetime.now(tz).strftime("%Y-%m-%d")
        elif args.date:
            date_filter = args.date

        with connect(DB_PATH) as conn:
            init_db(conn)
            run_id, edges = score_latest_prematch(conn)
            if run_id == 0:
                print("Prematch bülteni bulunamadı.")
                return 1
            coupons = build_coupons(
                edges,
                top_n=args.top,
                min_combined_odd=args.min_odd,
                date_filter=date_filter,
            )
            save_coupon_candidates(conn, run_id, coupons)

        if not coupons:
            print("Uygun kupon bulunamadı.")
            return 0
        print(f"run_id={run_id}  {len(coupons)} kupon üretildi\n")
        for rank, coupon in enumerate(coupons, 1):
            print(format_coupon(rank, coupon))
            print()
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
