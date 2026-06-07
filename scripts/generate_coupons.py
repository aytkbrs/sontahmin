"""Pipeline script — runs hourly via GitHub Actions.

Akıllı diff mantığı:
  - iddaa bülteni değişmediyse (version aynı) → stats/scoring/kupon atlanır,
    sadece live skor + etiket toplama yapılır, JSON'da zaman damgası güncellenir
  - Yeni gün başladıysa veya bülten değiştiyse → tam pipeline çalışır

Bu sayede DB gereksiz büyümez, API'ye boşu boşuna istek gitmez.
"""

from __future__ import annotations

import datetime
import json
import os
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
from src.iddaa_ingest.db import insert_external_odds
from src.iddaa_ingest.team_profiles import (
    update_profiles_from_labels,
    get_team_profile,
    blend_api_and_profile,
    init_team_schema,
)
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


def _fetch_external_odds(run_id: int, event_rows: list) -> None:
    """Pinnacle / keskin bahisçi oranlarını çek ve DB'ye kaydet."""
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("      [odds-api] ODDS_API_KEY yok — dış oranlar atlanıyor.")
        return
    try:
        from src.iddaa_ingest.odds_api import fetch_and_match
        iddaa_events = [
            {
                "event_id": r["event_id"],
                "home_name": r["home_name"],
                "away_name": r["away_name"],
                "event_epoch": None,  # epoch sonra join ile bulunabilir
            }
            for r in event_rows
        ]
        # event_epoch'u ekle
        with connect(DB_PATH) as conn:
            for i, r in enumerate(event_rows):
                ep = conn.execute(
                    "SELECT event_epoch FROM event_snapshots WHERE run_id=? AND event_id=?",
                    (run_id, r["event_id"]),
                ).fetchone()
                if ep:
                    iddaa_events[i]["event_epoch"] = ep["event_epoch"]

        records = fetch_and_match(iddaa_events, api_key=api_key)
        if records:
            with connect(DB_PATH) as conn:
                init_db(conn)
                insert_external_odds(conn, run_id, records)
            matched = sum(1 for r in records if r.iddaa_event_id is not None)
            print(f"      [odds-api] {len(records)} maç · {matched} iddaa eşleşmesi · DB'ye kaydedildi")
    except Exception as exc:
        print(f"      [odds-api] Hata: {exc}")


def _fetch_live_and_label() -> tuple[int, int, int, int]:
    """Canlı skor çek, biten maçları etiketle, takım profillerini güncelle."""
    try:
        live_result = ingest_live_scoreboard()
        with connect(DB_PATH) as conn:
            init_db(conn)
            init_team_schema(conn)
            labels = generate_result_labels(conn)
            training = build_prematch_training_dataset(conn)
            profile_rows = update_profiles_from_labels(conn)
        return live_result.event_count, labels, training, profile_rows
    except Exception as exc:
        print(f"      [uyarı] live skor hatası: {exc}")
        return 0, 0, 0, 0


def _accumulation_stats() -> dict:
    today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
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
        pinnacle_covered = conn.execute(
            """
            SELECT COUNT(*) FROM external_odds_snapshots
            WHERE iddaa_event_id IS NOT NULL
              AND date(fetched_at) = ?
            """,
            (today,),
        ).fetchone()[0]
        try:
            n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
            n_team_matches = conn.execute("SELECT COUNT(*) FROM team_match_results").fetchone()[0] // 2
        except Exception:
            n_teams = 0
            n_team_matches = 0
    return {
        "total_prematch_runs": total_runs,
        "total_result_labels": total_labels,
        "total_training_rows": total_training,
        "pinnacle_covered_today": pinnacle_covered,
        "tracked_teams": n_teams,
        "team_match_history": n_team_matches,
    }


def run() -> None:
    today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    now_str = datetime.datetime.now(TZ).isoformat(timespec="seconds")
    print(f"\n=== iddaa Pipeline {now_str} ===\n")

    # 1 — Prematch bulletin (diff check built in)
    print("[1/?] Prematch bülteni kontrol ediliyor...")
    ingest_result = ingest_prematch_football()
    run_id = ingest_result.run_id

    if ingest_result.changed:
        print(f"      DEĞİŞTİ → run_id={run_id}  events={ingest_result.event_count}")
    else:
        print(f"      DEĞİŞMEDİ → run_id={run_id} (mevcut)")

    # Determine whether full pipeline is needed
    existing_json: dict | None = None
    if OUTPUT.exists():
        try:
            with open(OUTPUT, encoding="utf-8") as f:
                existing_json = json.load(f)
        except Exception:
            existing_json = None

    last_json_date = existing_json.get("date", "") if existing_json else ""
    new_day = (last_json_date != today)

    need_full_pipeline = ingest_result.changed or new_day

    if not need_full_pipeline:
        # Bulletin unchanged, same day → only update timestamp + live scores
        print("\nBülten değişmedi, yeni gün yok → sadece skor ve zaman güncelleniyor.")
        live_count, labels, training, profile_rows = _fetch_live_and_label()
        print(f"      {live_count} canlı maç · {labels} etiket · {training} training · {profile_rows} profil")

        if existing_json:
            existing_json["last_checked"] = now_str
            existing_json["accumulation"] = _accumulation_stats()
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT, "w", encoding="utf-8") as f:
                json.dump(existing_json, f, ensure_ascii=False, indent=2)
            print(f"\n✓ Zaman damgası güncellendi: {now_str}")
        return

    # Full pipeline below
    print(f"\nTam pipeline çalışıyor (yeni_gün={new_day}, değişti={ingest_result.changed})")

    # 2 — Form statistics
    print(f"\n[2/5] Form istatistikleri çekiliyor ({ingest_result.event_count} maç)...")
    sc = StatsClient()
    with connect(DB_PATH) as conn:
        init_db(conn)
        event_rows = conn.execute(
            "SELECT event_id, home_name, away_name FROM event_snapshots WHERE run_id=?",
            (run_id,),
        ).fetchall()

    # Takım profillerini önceden yükle (tek sorgulama, her maç için tekrar bağlanma)
    with connect(DB_PATH) as _pconn:
        init_db(_pconn)
        init_team_schema(_pconn)
        home_profiles = {ev["home_name"]: get_team_profile(_pconn, ev["home_name"] or "") for ev in event_rows}
        away_profiles = {ev["away_name"]: get_team_profile(_pconn, ev["away_name"] or "") for ev in event_rows}
    profile_used = sum(1 for p in home_profiles.values() if p is not None)
    print(f"      Takım profili: {profile_used}/{len(event_rows)} ev takımı için geçmiş veri mevcut")

    snaps: list[_StatsSnap] = []
    for ev in event_rows:
        eid = ev["event_id"]
        try:
            r = sc.fetch_card_corners(eid)
            if not r.get("isSuccess"):
                raise RuntimeError("isSuccess=false")
            ms = extract_match_stats(eid, r.get("data", {}))

            # Takım profili varsa API verisini blend et
            hp = home_profiles.get(ev["home_name"])
            ap = away_profiles.get(ev["away_name"])

            if ms.has_data and (hp or ap):
                from src.iddaa_ingest.stats import TeamStats, MatchStats as MS2
                blended_home = TeamStats(
                    name=ms.home.name,
                    n_matches=ms.home.n_matches + (hp.n_home if hp else 0),
                    avg_scored=blend_api_and_profile(
                        ms.home.avg_scored, ms.home.n_matches,
                        hp.home_scored if hp else ms.home.avg_scored,
                        hp.n_home if hp else 0,
                    ),
                    avg_conceded=blend_api_and_profile(
                        ms.home.avg_conceded, ms.home.n_matches,
                        hp.home_conceded if hp else ms.home.avg_conceded,
                        hp.n_home if hp else 0,
                    ),
                    wins=ms.home.wins, draws=ms.home.draws, losses=ms.home.losses,
                    avg_corners=ms.home.avg_corners, avg_yellow_cards=ms.home.avg_yellow_cards,
                )
                blended_away = TeamStats(
                    name=ms.away.name,
                    n_matches=ms.away.n_matches + (ap.n_away if ap else 0),
                    avg_scored=blend_api_and_profile(
                        ms.away.avg_scored, ms.away.n_matches,
                        ap.away_scored if ap else ms.away.avg_scored,
                        ap.n_away if ap else 0,
                    ),
                    avg_conceded=blend_api_and_profile(
                        ms.away.avg_conceded, ms.away.n_matches,
                        ap.away_conceded if ap else ms.away.avg_conceded,
                        ap.n_away if ap else 0,
                    ),
                    wins=ms.away.wins, draws=ms.away.draws, losses=ms.away.losses,
                    avg_corners=ms.away.avg_corners, avg_yellow_cards=ms.away.avg_yellow_cards,
                )
                ms_blended = MS2(event_id=eid, home=blended_home, away=blended_away, has_data=True)
                probs = compute_match_probs(ms_blended)
            else:
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

    # 2b — External odds (Pinnacle / sharp bookmakers) — günde 1 kez
    _fetch_external_odds(run_id, event_rows)

    # 3 — Edge scoring + odds drift
    print("[3/5] Edge scoring (Poisson + odds drift)...")
    with connect(DB_PATH) as conn:
        init_db(conn)
        _, edges = score_latest_prematch(conn)
        for edge in edges:
            insert_edge_score(conn, edge)
    drift_count = sum(1 for e in edges if e.drift_home is not None)
    print(f"      {len(edges)} maç · {drift_count} drift hesaplandı")

    # 4 — Coupons (only open markets, today's matches)
    print(f"[4/5] Bugün ({today}) için kupon üretiliyor...")
    coupons = build_coupons(edges, top_n=TOP_COUPONS, date_filter=today)
    with connect(DB_PATH) as conn:
        save_coupon_candidates(conn, run_id, coupons)
    print(f"      {len(coupons)} kupon üretildi")

    # 5 — Live scores + labels + training + team profiles
    print("[5/5] Canlı skor + maç sonuçları + takım profilleri...")
    live_count, labels, training, profile_rows = _fetch_live_and_label()
    print(f"      {live_count} canlı maç · {labels} etiket · {training} training · {profile_rows} yeni profil satırı")

    # Serialize
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

    payload = {
        "generated_at": now_str,
        "last_checked": now_str,
        "date": today,
        "run_id": run_id,
        "total_events": ingest_result.event_count,
        "events_with_stats": with_stats,
        "coupons": coupon_list,
        "accumulation": _accumulation_stats(),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    acc = payload["accumulation"]
    print(f"\n✓ Kaydedildi: {OUTPUT}")
    print(
        f"✓ Birikim: {acc['total_prematch_runs']} gün · "
        f"{acc['total_result_labels']} etiket · "
        f"{acc['total_training_rows']} training satırı"
    )
    print(f"✓ Tamamlandı: {datetime.datetime.now(TZ).isoformat(timespec='seconds')}\n")


if __name__ == "__main__":
    run()
