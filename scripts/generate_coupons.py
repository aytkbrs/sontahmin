"""Pipeline script — runs daily via GitHub Actions.

Fetches prematch bulletin, form statistics, computes Poisson-based edge
scores (with odds drift from previous run), builds today's 3-leg coupons,
captures live scores (settled matches), generates result labels and training
rows, then writes output/coupons_today.json for the Streamlit frontend.

Over time the DB accumulates: every day adds bulletin snapshots, settled
labels, and training rows — the model can then be calibrated with real data.
"""

from __future__ import annotations

import datetime
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.iddaa_ingest.client import StatsClient
from src.iddaa_ingest.config import DB_PATH
from src.iddaa_ingest.coupon import OUTCOME_NAMES, build_coupons
from src.iddaa_ingest.db import (
    connect,
    init_db,
    insert_edge_score,
    save_coupon_candidates,
    upsert_event_stats,
)
from src.iddaa_ingest.ingest import ingest_prematch_football, ingest_live_scoreboard
from src.iddaa_ingest.labels import generate_result_labels, build_prematch_training_dataset
from src.iddaa_ingest.model import score_latest_prematch
from src.iddaa_ingest.poisson import compute_match_probs
from src.iddaa_ingest.stats import extract_match_stats

TZ = datetime.timezone(datetime.timedelta(hours=3))
OUTPUT = ROOT / "output" / "coupons_today.json"
STATS_DELAY = 0.35
TOP_COUPONS = 5


@dataclass
class _StatsSnap:
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


def _empty_snap(eid: int, home: str | None, away: str | None) -> _StatsSnap:
    return _StatsSnap(
        event_id=eid, home_name=home, away_name=away,
        home_n_matches=0, home_avg_scored=None, home_avg_conceded=None,
        home_wins=None, home_draws=None, home_losses=None,
        away_n_matches=0, away_avg_scored=None, away_avg_conceded=None,
        away_wins=None, away_draws=None, away_losses=None,
        lambda_home=None, lambda_away=None,
        model_p1=None, model_px=None, model_p2=None,
        model_p_over25=None, model_p_btts=None, has_data=False,
    )


def _leg_to_dict(leg) -> dict:
    epoch = leg.event_epoch
    if epoch:
        match_dt = datetime.datetime.fromtimestamp(epoch, tz=TZ)
        match_time = match_dt.strftime("%H:%M")
        match_datetime = match_dt.isoformat(timespec="minutes")
    else:
        match_time = "?"
        match_datetime = "?"
    return {
        "event_id": leg.event_id,
        "home": leg.home_name or "?",
        "away": leg.away_name or "?",
        "outcome_key": leg.outcome,
        "outcome_label": OUTCOME_NAMES.get(leg.outcome, leg.outcome),
        "odd": round(leg.odd, 2),
        "fair_prob": round(leg.fair_prob, 4),
        "drift": round(leg.drift, 4),
        "match_time": match_time,
        "match_datetime": match_datetime,
    }


def run() -> None:
    today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    now_str = datetime.datetime.now(TZ).isoformat(timespec="seconds")
    print(f"\n=== iddaa Pipeline {now_str} ===\n")

    # 1 — Prematch bulletin
    print("[1/5] Prematch bülteni çekiliyor...")
    ingest_result = ingest_prematch_football()
    run_id = ingest_result.run_id
    print(f"      run_id={run_id}  events={ingest_result.event_count}")

    # 2 — Form statistics (Poisson model inputs)
    print(f"[2/5] Form istatistikleri çekiliyor ({ingest_result.event_count} maç)...")
    sc = StatsClient()
    with connect(DB_PATH) as conn:
        init_db(conn)
        event_rows = conn.execute(
            "SELECT event_id, home_name, away_name FROM event_snapshots WHERE run_id=?",
            (run_id,),
        ).fetchall()

    snaps: list[_StatsSnap] = []
    for i, ev in enumerate(event_rows):
        eid = ev["event_id"]
        try:
            r = sc.fetch_card_corners(eid)
            if not r.get("isSuccess"):
                raise RuntimeError("isSuccess=false")
            ms = extract_match_stats(eid, r.get("data", {}))
            probs = compute_match_probs(ms) if ms.has_data else None
            snap = _StatsSnap(
                event_id=eid,
                home_name=ev["home_name"],
                away_name=ev["away_name"],
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
        except Exception:
            snap = _empty_snap(eid, ev["home_name"], ev["away_name"])
        snaps.append(snap)
        time.sleep(STATS_DELAY)

    with connect(DB_PATH) as conn:
        init_db(conn)
        for snap in snaps:
            upsert_event_stats(conn, run_id, snap)

    with_stats = sum(1 for s in snaps if s.has_data)
    print(f"      {with_stats}/{len(snaps)} maç için istatistik mevcut")

    # 3 — Edge scoring (with odds drift from previous run)
    print("[3/5] Edge scoring (Poisson model + odds drift)...")
    with connect(DB_PATH) as conn:
        init_db(conn)
        _, edges = score_latest_prematch(conn)
        for edge in edges:
            insert_edge_score(conn, edge)
    drift_count = sum(1 for e in edges if e.drift_home is not None)
    print(f"      {len(edges)} maç skorlandı · {drift_count} maç için oran kayması hesaplandı")

    # 4 — Build coupons for today
    print(f"[4/5] Bugün ({today}) için kupon üretiliyor...")
    coupons = build_coupons(edges, top_n=TOP_COUPONS, date_filter=today)
    with connect(DB_PATH) as conn:
        save_coupon_candidates(conn, run_id, coupons)
    print(f"      {len(coupons)} kupon üretildi")

    # 5 — Live scores: capture settled matches, generate labels, update training data
    print("[5/5] Canlı skor + maç sonuçları işleniyor...")
    try:
        live_result = ingest_live_scoreboard()
        with connect(DB_PATH) as conn:
            init_db(conn)
            labels_count = generate_result_labels(conn)
            training_count = build_prematch_training_dataset(conn)
        print(
            f"      {live_result.event_count} canlı maç · "
            f"{labels_count} yeni sonuç etiketi · "
            f"{training_count} training satırı"
        )
    except Exception as exc:
        print(f"      [uyarı] live skor çekme hatası: {exc}")
        labels_count = 0
        training_count = 0

    # Serialize output
    coupon_list = []
    for rank, c in enumerate(coupons, 1):
        coupon_list.append({
            "rank": rank,
            "combined_odd": round(c.combined_odd, 2),
            "win_prob": round(c.combined_fair_prob, 4),
            "expected_value": round(c.expected_value, 3),
            "drift_bonus": round(c.drift_bonus, 4),
            "legs": [_leg_to_dict(lg) for lg in c.legs],
        })

    # Accumulation stats (how much historical data we have)
    with connect(DB_PATH) as conn:
        total_runs = conn.execute(
            "SELECT COUNT(*) FROM ingest_runs WHERE bulletin_type=0"
        ).fetchone()[0]
        total_labels = conn.execute(
            "SELECT COUNT(*) FROM event_result_labels"
        ).fetchone()[0]
        total_training = conn.execute(
            "SELECT COUNT(*) FROM training_dataset_prematch"
        ).fetchone()[0]

    payload = {
        "generated_at": now_str,
        "date": today,
        "run_id": run_id,
        "total_events": ingest_result.event_count,
        "events_with_stats": with_stats,
        "coupons": coupon_list,
        "accumulation": {
            "total_prematch_runs": total_runs,
            "total_result_labels": total_labels,
            "total_training_rows": total_training,
        },
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Kaydedildi: {OUTPUT}")
    print(
        f"✓ Birikim: {total_runs} gün · "
        f"{total_labels} etiket · "
        f"{total_training} training satırı"
    )
    print(f"✓ Tamamlandı: {datetime.datetime.now(TZ).isoformat(timespec='seconds')}\n")


if __name__ == "__main__":
    run()
