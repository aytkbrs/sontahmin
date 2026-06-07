"""Fair probability estimation and per-event edge scoring.

Layer 1 of the model stack.  At this stage we use proportional margin
removal to convert bookmaker odds into fair probabilities.  This means
`fair_prob * odd == 1/overround` for every outcome — all bets have the
same expected value within a market.  The real edge layer (Layer 2) will
replace these with calibrated model probabilities once labelled historical
data has accumulated.  The interfaces below are designed so that swap is
a single-function change.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field


OUTCOME_NAMES = {
    "home": "1 (ev sahibi)",
    "draw": "X (beraberlik)",
    "away": "2 (deplasman)",
    "over": "ÜST 2.5",
    "under": "ALT 2.5",
    "btts_yes": "KG VAR",
    "btts_no": "KG YOK",
}


@dataclass(slots=True)
class CandidateLeg:
    event_id: int
    competition_id: int | None
    home_name: str | None
    away_name: str | None
    event_epoch: int | None
    market: str          # '1x2' | 'ou25' | 'btts'
    outcome: str         # 'home' | 'draw' | 'away' | 'over' | 'under' | 'btts_yes' | 'btts_no'
    odd: float
    fair_prob: float
    overround: float
    # drift > 0 → market moved toward this outcome since last run (confirming signal)
    # drift < 0 → market moved away (contrarian signal)
    # drift == 0 → no history available
    drift: float = 0.0


@dataclass(slots=True)
class EventEdge:
    run_id: int
    event_id: int
    competition_id: int | None
    home_name: str | None
    away_name: str | None
    event_epoch: int | None
    overround_1x2: float | None
    fair_home: float | None
    fair_draw: float | None
    fair_away: float | None
    fair_ou25_over: float | None
    fair_ou25_under: float | None
    fair_btts_yes: float | None
    fair_btts_no: float | None
    drift_home: float | None
    drift_draw: float | None
    drift_away: float | None
    best_leg: CandidateLeg | None
    all_legs: list[CandidateLeg] = field(default_factory=list)


_MIN_VALID_ODD = 1.01  # odds at or below this are treated as missing/invalid


def _fair_probs(odds: list[float | None]) -> list[float | None]:
    """Proportional margin removal.

    Requires ALL odds to be valid (> _MIN_VALID_ODD).  Returns a list of
    Nones when any odd is missing so callers can reject incomplete markets.
    """
    if any(o is None or o <= _MIN_VALID_ODD for o in odds):
        return [None] * len(odds)
    total = sum(1.0 / o for o in odds)  # type: ignore[arg-type]
    return [(1.0 / o) / total for o in odds]  # type: ignore[operator]


def _overround(odds: list[float | None]) -> float | None:
    if any(o is None or o <= _MIN_VALID_ODD for o in odds):
        return None
    return sum(1.0 / o for o in odds)  # type: ignore[arg-type]


def compute_event_edge(
    run_id: int,
    row: sqlite3.Row,
    model_probs=None,
    prev_implied: dict | None = None,
) -> EventEdge:
    """Compute edge scores for one event.

    Parameters
    ----------
    model_probs : MatchProbs | None
        If provided (from Poisson model), use these as fair probabilities
        instead of proportional margin removal.
    prev_implied : dict | None
        Previous run's bookmaker implied probs for this event:
        {'home': float, 'draw': float, 'away': float}.
        Used to compute odds drift — change in implied probability since
        the last prematch run for the same event.
    """
    event_id = row["event_id"]
    comp_id = row["competition_id"]
    home = row["home_name"]
    away = row["away_name"]
    epoch = row["event_epoch"]

    h, d, a = row["home_odd"], row["draw_odd"], row["away_odd"]
    ov_1x2 = _overround([h, d, a])

    ou_over, ou_under = row["ou25_over_odd"], row["ou25_under_odd"]
    btts_yes, btts_no = row["btts_yes_odd"], row["btts_no_odd"]

    # Compute bookmaker implied probs (independent of model) for drift tracking
    curr_h_impl = ((1 / h) / ov_1x2) if (h and h > _MIN_VALID_ODD and ov_1x2) else None
    curr_d_impl = ((1 / d) / ov_1x2) if (d and d > _MIN_VALID_ODD and ov_1x2) else None
    curr_a_impl = ((1 / a) / ov_1x2) if (a and a > _MIN_VALID_ODD and ov_1x2) else None

    # Odds drift: positive = market moved TOWARD this outcome (bookmaker lowers odds)
    drift_map: dict[str, float] = {}
    drift_home = drift_draw = drift_away = None
    if prev_implied and curr_h_impl is not None:
        drift_home = curr_h_impl - prev_implied.get("home", curr_h_impl)
        drift_draw = (curr_d_impl - prev_implied.get("draw", curr_d_impl)) if curr_d_impl is not None else None
        drift_away = (curr_a_impl - prev_implied.get("away", curr_a_impl)) if curr_a_impl is not None else None
        for k, v in [("home", drift_home), ("draw", drift_draw), ("away", drift_away)]:
            if v is not None:
                drift_map[k] = v

    # Use Poisson model probs when available; fall back to margin removal
    if model_probs is not None:
        fh, fd, fa = model_probs.p1, model_probs.px, model_probs.p2
        f_over, f_under = model_probs.p_over25, model_probs.p_under25
        f_yes, f_no = model_probs.p_btts, model_probs.p_no_btts
    else:
        fh, fd, fa = _fair_probs([h, d, a])
        f_over, f_under = _fair_probs([ou_over, ou_under])
        f_yes, f_no = _fair_probs([btts_yes, btts_no])

    def _legs_model(market: str, keys: list, odds: list, fairs: list) -> list[CandidateLeg]:
        ov = _overround(odds)
        if ov is None:
            return []
        result = []
        for key, odd, fair in zip(keys, odds, fairs):
            if odd is None or fair is None:
                continue
            if odd < _MIN_VALID_ODD:
                continue
            if odd < 1.30 or odd > 6.00:
                continue
            if fair < 0.28:
                continue
            result.append(CandidateLeg(
                event_id=event_id, competition_id=comp_id,
                home_name=home, away_name=away, event_epoch=epoch,
                market=market, outcome=key, odd=odd, fair_prob=fair, overround=ov,
                drift=drift_map.get(key, 0.0),
            ))
        return result

    all_legs: list[CandidateLeg] = []
    all_legs.extend(_legs_model("1x2", ["home", "draw", "away"], [h, d, a], [fh, fd, fa]))
    all_legs.extend(_legs_model("ou25", ["over", "under"], [ou_over, ou_under], [f_over, f_under]))
    all_legs.extend(_legs_model("btts", ["btts_yes", "btts_no"], [btts_yes, btts_no], [f_yes, f_no]))

    # Best leg: when using model probs, prefer highest edge (fair_prob * odd - 1)
    # Without model probs, fall back to highest fair_prob
    if model_probs is not None:
        best_leg = max(all_legs, key=lambda lg: lg.fair_prob * lg.odd - 1) if all_legs else None
    else:
        best_leg = max(all_legs, key=lambda lg: lg.fair_prob) if all_legs else None

    return EventEdge(
        run_id=run_id,
        event_id=event_id,
        competition_id=comp_id,
        home_name=home,
        away_name=away,
        event_epoch=epoch,
        overround_1x2=ov_1x2,
        fair_home=fh,
        fair_draw=fd,
        fair_away=fa,
        fair_ou25_over=f_over,
        fair_ou25_under=f_under,
        fair_btts_yes=f_yes,
        fair_btts_no=f_no,
        drift_home=drift_home,
        drift_draw=drift_draw,
        drift_away=drift_away,
        best_leg=best_leg,
        all_legs=all_legs,
    )


def score_latest_prematch(conn: sqlite3.Connection) -> tuple[int, list[EventEdge]]:
    """Return (run_id, list[EventEdge]) for the latest prematch bulletin.

    Uses Poisson model probabilities from event_stats_snapshots when available,
    otherwise falls back to proportional margin removal.

    Also computes odds drift by comparing with the previous prematch run for
    each event_id that appears in both runs.
    """
    from .poisson import MatchProbs as PoissonProbs

    run = conn.execute(
        "SELECT id FROM ingest_runs WHERE bulletin_type = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return 0, []
    run_id = run["id"]

    # Previous prematch run for drift computation
    prev_run = conn.execute(
        "SELECT id FROM ingest_runs WHERE bulletin_type = 0 AND id < ? ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    prev_implied: dict[int, dict[str, float]] = {}
    if prev_run:
        prev_fs = conn.execute(
            "SELECT event_id, implied_home, implied_draw, implied_away "
            "FROM event_feature_snapshots WHERE run_id = ?",
            (prev_run["id"],),
        ).fetchall()
        for pf in prev_fs:
            if pf["implied_home"] is not None:
                prev_implied[pf["event_id"]] = {
                    "home": pf["implied_home"],
                    "draw": pf["implied_draw"],
                    "away": pf["implied_away"],
                }

    rows = conn.execute(
        """
        SELECT
            fs.event_id,
            es.competition_id,
            es.home_name,
            es.away_name,
            es.event_epoch,
            fs.home_odd, fs.draw_odd, fs.away_odd,
            fs.ou25_over_odd, fs.ou25_under_odd,
            fs.btts_yes_odd, fs.btts_no_odd,
            ss.lambda_home, ss.lambda_away,
            ss.model_p1, ss.model_px, ss.model_p2,
            ss.model_p_over25, ss.model_p_btts,
            ss.has_data AS stats_available,
            ext.home_odd  AS ext_home_odd,
            ext.draw_odd  AS ext_draw_odd,
            ext.away_odd  AS ext_away_odd,
            ext.bookmaker AS ext_bookmaker,
            ext.match_confidence AS ext_confidence
        FROM event_feature_snapshots fs
        JOIN event_snapshots es
          ON es.run_id = fs.run_id AND es.event_id = fs.event_id
        LEFT JOIN event_stats_snapshots ss
          ON ss.run_id = fs.run_id AND ss.event_id = fs.event_id
        LEFT JOIN external_odds_snapshots ext
          ON ext.run_id = fs.run_id AND ext.iddaa_event_id = fs.event_id
        WHERE fs.run_id = ?
          AND fs.has_main_1x2 = 1
        ORDER BY fs.event_id
        """,
        (run_id,),
    ).fetchall()

    edges = []
    for r in rows:
        model_probs = None

        # Priority 1: Pinnacle / sharp bookmaker odds (most reliable edge signal)
        ext_h = r["ext_home_odd"]
        ext_d = r["ext_draw_odd"]
        ext_a = r["ext_away_odd"]
        ext_conf = r["ext_confidence"] or 0.0
        if (
            ext_h and ext_d and ext_a
            and ext_h > 1.01 and ext_d > 1.01 and ext_a > 1.01
            and ext_conf >= 0.72
        ):
            ext_ov = 1 / ext_h + 1 / ext_d + 1 / ext_a
            model_probs = PoissonProbs(
                lambda_home=None,
                lambda_away=None,
                p1=(1 / ext_h) / ext_ov,
                px=(1 / ext_d) / ext_ov,
                p2=(1 / ext_a) / ext_ov,
                # OU/BTTS: fall back to Poisson if available, else None
                p_over25=r["model_p_over25"] if r["stats_available"] else 0.5,
                p_under25=1.0 - (r["model_p_over25"] if r["stats_available"] else 0.5),
                p_btts=r["model_p_btts"] if r["stats_available"] else 0.5,
                p_no_btts=1.0 - (r["model_p_btts"] if r["stats_available"] else 0.5),
            )

        # Priority 2: Poisson model (if Pinnacle not available)
        elif r["stats_available"] and r["model_p1"] is not None:
            model_probs = PoissonProbs(
                lambda_home=r["lambda_home"],
                lambda_away=r["lambda_away"],
                p1=r["model_p1"],
                px=r["model_px"],
                p2=r["model_p2"],
                p_over25=r["model_p_over25"],
                p_under25=1.0 - r["model_p_over25"],
                p_btts=r["model_p_btts"],
                p_no_btts=1.0 - r["model_p_btts"],
            )

        # Priority 3: margin removal only (no model)

        pi = prev_implied.get(r["event_id"])
        edges.append(compute_event_edge(run_id, r, model_probs, prev_implied=pi))
    return run_id, edges
