from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EventFeatures:
    event_id: int
    market_count: int
    outcome_count: int
    has_main_1x2: bool
    has_ou25: bool
    has_btts: bool
    home_odd: float | None
    draw_odd: float | None
    away_odd: float | None
    implied_home: float | None
    implied_draw: float | None
    implied_away: float | None
    overround_1x2: float | None
    favorite_side: str | None
    favorite_odd: float | None
    favorite_gap_12: float | None
    ou25_under_odd: float | None
    ou25_over_odd: float | None
    btts_yes_odd: float | None
    btts_no_odd: float | None
    goal_bias_score: float | None
    draw_pressure_score: float | None
    parity_score: float | None
    market_richness_score: float


def _implied_prob(odd: float | None) -> float | None:
    if odd is None or odd <= 0:
        return None
    return 1.0 / odd


def _find_market(event: dict, *, market_type: int, market_subtype: int) -> dict | None:
    for market in event.get("m", []):
        if market.get("t") == market_type and market.get("st") == market_subtype:
            return market
    return None


def _find_outcome_odd(market: dict | None, outcome_name: str) -> float | None:
    if market is None:
        return None
    for outcome in market.get("o", []):
        if outcome.get("n") == outcome_name:
            return outcome.get("odd")
    return None


def _find_outcome_no_odd(market: dict | None, outcome_no: int) -> float | None:
    if market is None:
        return None
    for outcome in market.get("o", []):
        if outcome.get("no") == outcome_no:
            return outcome.get("odd")
    return None


def build_event_features(event: dict) -> EventFeatures:
    markets = event.get("m", [])
    outcome_count = sum(len(market.get("o", [])) for market in markets)

    main_1x2 = _find_market(event, market_type=1, market_subtype=1)
    ou25 = _find_market(event, market_type=2, market_subtype=101)
    btts = _find_market(event, market_type=2, market_subtype=89)

    home_odd = _find_outcome_no_odd(main_1x2, 1)
    draw_odd = _find_outcome_no_odd(main_1x2, 2)
    away_odd = _find_outcome_no_odd(main_1x2, 3)

    implied_home = _implied_prob(home_odd)
    implied_draw = _implied_prob(draw_odd)
    implied_away = _implied_prob(away_odd)

    overround_1x2 = None
    if None not in (implied_home, implied_draw, implied_away):
        overround_1x2 = implied_home + implied_draw + implied_away

    favorite_side = None
    favorite_odd = None
    candidate_odds = {
        "home": home_odd,
        "draw": draw_odd,
        "away": away_odd,
    }
    valid_candidate_odds = {k: v for k, v in candidate_odds.items() if v is not None}
    if valid_candidate_odds:
        favorite_side = min(valid_candidate_odds, key=valid_candidate_odds.get)
        favorite_odd = valid_candidate_odds[favorite_side]

    favorite_gap_12 = None
    two_way = sorted(v for v in (home_odd, away_odd) if v is not None)
    if len(two_way) == 2:
        favorite_gap_12 = two_way[1] - two_way[0]

    ou25_under_odd = _find_outcome_no_odd(ou25, 1)
    ou25_over_odd = _find_outcome_no_odd(ou25, 2)
    btts_yes_odd = _find_outcome_odd(btts, "Var")
    btts_no_odd = _find_outcome_odd(btts, "Yok")

    goal_bias_score = None
    if ou25_under_odd is not None and ou25_over_odd is not None:
        goal_bias_score = _implied_prob(ou25_over_odd) - _implied_prob(ou25_under_odd)

    draw_pressure_score = None
    if None not in (implied_home, implied_draw, implied_away):
        total = implied_home + implied_draw + implied_away
        if total > 0:
            draw_pressure_score = implied_draw / total

    parity_score = None
    if implied_home is not None and implied_away is not None:
        parity_score = 1.0 - abs(implied_home - implied_away)

    market_richness_score = len(markets) + (outcome_count / 10.0)

    return EventFeatures(
        event_id=event["i"],
        market_count=len(markets),
        outcome_count=outcome_count,
        has_main_1x2=main_1x2 is not None,
        has_ou25=ou25 is not None,
        has_btts=btts is not None,
        home_odd=home_odd,
        draw_odd=draw_odd,
        away_odd=away_odd,
        implied_home=implied_home,
        implied_draw=implied_draw,
        implied_away=implied_away,
        overround_1x2=overround_1x2,
        favorite_side=favorite_side,
        favorite_odd=favorite_odd,
        favorite_gap_12=favorite_gap_12,
        ou25_under_odd=ou25_under_odd,
        ou25_over_odd=ou25_over_odd,
        btts_yes_odd=btts_yes_odd,
        btts_no_odd=btts_no_odd,
        goal_bias_score=goal_bias_score,
        draw_pressure_score=draw_pressure_score,
        parity_score=parity_score,
        market_richness_score=market_richness_score,
    )
