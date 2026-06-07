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


def _make_legs(
    event_id: int,
    competition_id: int | None,
    home_name: str | None,
    away_name: str | None,
    event_epoch: int | None,
    market: str,
    outcome_keys: list[str],
    odds: list[float | None],
    min_odd: float = 1.30,
    max_odd: float = 6.00,
    min_fair_prob: float = 0.28,
) -> list[CandidateLeg]:
    fairs = _fair_probs(odds)
    ov = _overround(odds)
    if ov is None:
        return []
    legs = []
    for key, odd, fair in zip(outcome_keys, odds, fairs):
        if odd is None or fair is None:
            continue
        if odd < min_odd or odd > max_odd:
            continue
        if fair < min_fair_prob:
            continue
        legs.append(
            CandidateLeg(
                event_id=event_id,
                competition_id=competition_id,
                home_name=home_name,
                away_name=away_name,
                event_epoch=event_epoch,
                market=market,
                outcome=key,
                odd=odd,
                fair_prob=fair,
                overround=ov,
            )
        )
    return legs


def compute_event_edge(run_id: int, row: sqlite3.Row, model_probs=None) -> EventEdge:
    """Compute edge scores for one event.

    Parameters
    ----------
    model_probs : MatchProbs | None
        If provided (from Poisson model), use these as fair probabilities
        instead of proportional margin removal.  This enables real edge
        detection vs the bookmaker.
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

    # Use Poisson model probs when available; fall back to margin removal
    if model_probs is not None:
        fh, fd, fa = model_probs.p1, model_probs.px, model_probs.p2
        f_over, f_under = model_probs.p_over25, model_probs.p_under25
        f_yes, f_no = model_probs.p_btts, model_probs.p_no_btts
    else:
        fh, fd, fa = _fair_probs([h, d, a])
        f_over, f_under = _fair_probs([ou_over, ou_under])
        f_yes, f_no = _fair_probs([btts_yes, btts_no])

    # Build candidate legs — use model fair_probs, not derived from odds
    def _legs_model(market: str, keys: list[str], odds: list, fairs: list) -> list[CandidateLeg]:
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
        best_leg=best_leg,
        all_legs=all_legs,
    )


def score_latest_prematch(conn: sqlite3.Connection) -> tuple[int, list[EventEdge]]:
    """Return (run_id, list[EventEdge]) for the latest prematch bulletin.

    Uses Poisson model probabilities from event_stats_snapshots when available,
    otherwise falls back to proportional margin removal.
    """
    from .poisson import MatchProbs as PoissonProbs

    run = conn.execute(
        "SELECT id FROM ingest_runs WHERE bulletin_type = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return 0, []
    run_id = run["id"]

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
            ss.has_data AS stats_available
        FROM event_feature_snapshots fs
        JOIN event_snapshots es
          ON es.run_id = fs.run_id AND es.event_id = fs.event_id
        LEFT JOIN event_stats_snapshots ss
          ON ss.run_id = fs.run_id AND ss.event_id = fs.event_id
        WHERE fs.run_id = ?
          AND fs.has_main_1x2 = 1
        ORDER BY fs.event_id
        """,
        (run_id,),
    ).fetchall()

    edges = []
    for r in rows:
        model_probs = None
        if r["stats_available"] and r["model_p1"] is not None:
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
        edges.append(compute_event_edge(run_id, r, model_probs))
    return run_id, edges
