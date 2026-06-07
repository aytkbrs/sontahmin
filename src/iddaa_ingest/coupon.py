"""Coupon assembly — Layer 3 of the model stack.

Takes edge-scored candidate legs and produces ranked 3-leg coupons.
Selection objective: maximise `combined_fair_prob` subject to a minimum
combined odd, while penalising correlated legs (same competition) and
rewarding odds drift confirmation (market moving toward our selected outcome).
"""

from __future__ import annotations

import datetime
import itertools
import math
from dataclasses import dataclass

from .model import CandidateLeg, EventEdge, OUTCOME_NAMES


@dataclass(slots=True)
class CouponLeg:
    event_id: int
    competition_id: int | None
    home_name: str | None
    away_name: str | None
    event_epoch: int | None
    outcome: str
    odd: float
    fair_prob: float
    overround: float
    drift: float = 0.0

    @property
    def label(self) -> str:
        match = f"{self.home_name or '?'} - {self.away_name or '?'}"
        return f"{match}  [{OUTCOME_NAMES.get(self.outcome, self.outcome)} @ {self.odd:.2f}]"


@dataclass(slots=True)
class Coupon:
    legs: list[CouponLeg]
    combined_odd: float
    combined_fair_prob: float
    expected_value: float
    log_ev: float
    same_competition_pairs: int
    drift_bonus: float
    final_score: float


def _build_leg(candidate: CandidateLeg) -> CouponLeg:
    return CouponLeg(
        event_id=candidate.event_id,
        competition_id=candidate.competition_id,
        home_name=candidate.home_name,
        away_name=candidate.away_name,
        event_epoch=candidate.event_epoch,
        outcome=candidate.outcome,
        odd=candidate.odd,
        fair_prob=candidate.fair_prob,
        overround=candidate.overround,
        drift=candidate.drift,
    )


def _score_coupon(legs: list[CouponLeg]) -> Coupon:
    combined_odd = 1.0
    combined_fair_prob = 1.0
    log_ev = 0.0
    for lg in legs:
        combined_odd *= lg.odd
        combined_fair_prob *= lg.fair_prob
        log_ev += math.log(lg.fair_prob * lg.odd)

    ev = combined_fair_prob * combined_odd - 1.0

    comp_ids = [lg.competition_id for lg in legs if lg.competition_id is not None]
    same_comp_pairs = sum(
        1 for a, b in itertools.combinations(comp_ids, 2) if a == b
    )

    # Drift bonus: reward legs where the market is also moving toward our selection.
    # Only applies when drift > 0 (market confirms our model's direction).
    # Scale: drift is in implied-prob units (~0.01-0.05), multiplied by 1.5.
    drift_bonus = sum(max(0.0, lg.drift) * 1.5 for lg in legs)

    # Penalise correlated legs (same competition), reward drift confirmation
    final_score = (
        math.log(max(combined_fair_prob, 1e-9))
        - 0.15 * same_comp_pairs
        + drift_bonus
    )

    return Coupon(
        legs=legs,
        combined_odd=combined_odd,
        combined_fair_prob=combined_fair_prob,
        expected_value=ev,
        log_ev=log_ev,
        same_competition_pairs=same_comp_pairs,
        drift_bonus=drift_bonus,
        final_score=final_score,
    )


def build_coupons(
    edges: list[EventEdge],
    *,
    top_n: int = 10,
    min_combined_odd: float = 2.50,
    max_events_to_search: int = 40,
    date_filter: str | None = None,
) -> list[Coupon]:
    """Build ranked coupons.

    Parameters
    ----------
    date_filter : "YYYY-MM-DD" string in local time (UTC+3).
        When set, only legs whose event_epoch falls on that calendar day
        are included.
    """
    # Optional date filter (UTC+3 Turkey local time)
    epoch_min: int | None = None
    epoch_max: int | None = None
    if date_filter:
        tz_offset = datetime.timezone(datetime.timedelta(hours=3))
        day = datetime.datetime.strptime(date_filter, "%Y-%m-%d").replace(tzinfo=tz_offset)
        epoch_min = int(day.timestamp())
        epoch_max = int((day + datetime.timedelta(days=1)).timestamp())

    # Collect one representative leg per event.
    # When model probs are used, rank by EV (fair_prob * odd - 1) so we pick
    # the outcome with the highest edge, not just the highest probability.
    # Fall back to fair_prob ranking when EV would be uniformly negative
    # (margin-removal-only mode).
    per_event: list[CouponLeg] = []
    for edge in edges:
        if not edge.all_legs:
            continue
        # Date filter
        if epoch_min is not None and edge.event_epoch is not None:
            if not (epoch_min <= edge.event_epoch < epoch_max):
                continue

        ev_vals = [lg.fair_prob * lg.odd - 1.0 for lg in edge.all_legs]
        if max(ev_vals) > 0:
            best = max(edge.all_legs, key=lambda lg: lg.fair_prob * lg.odd - 1.0)
        else:
            best = max(edge.all_legs, key=lambda lg: lg.fair_prob)
        per_event.append(_build_leg(best))

    # Limit search space to top events by fair_prob
    per_event.sort(key=lambda lg: lg.fair_prob, reverse=True)
    pool = per_event[:max_events_to_search]

    if len(pool) < 3:
        return []

    coupons: list[Coupon] = []
    for trio in itertools.combinations(pool, 3):
        combined_odd = trio[0].odd * trio[1].odd * trio[2].odd
        if combined_odd < min_combined_odd:
            continue
        coupon = _score_coupon(list(trio))
        coupons.append(coupon)

    coupons.sort(key=lambda c: c.final_score, reverse=True)
    return coupons[:top_n]


def format_coupon(rank: int, coupon: Coupon) -> str:
    drift_str = f"  drift_bonus={coupon.drift_bonus:+.3f}" if coupon.drift_bonus != 0 else ""
    lines = [
        f"Kupon #{rank}  "
        f"combined_odd={coupon.combined_odd:.2f}  "
        f"win_prob={coupon.combined_fair_prob:.1%}  "
        f"EV={coupon.expected_value:+.2f}"
        f"{drift_str}",
    ]
    for i, leg in enumerate(coupon.legs, 1):
        drift_tag = f"  [drift {leg.drift:+.3f}]" if leg.drift != 0 else ""
        lines.append(f"  Ayak {i}: {leg.label}{drift_tag}")
    return "\n".join(lines)
