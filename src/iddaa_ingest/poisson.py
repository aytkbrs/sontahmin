"""Poisson-based match probability model (Dixon-Coles 1997, simplified).

Given team attack/defense ratings derived from recent match data, computes
probabilities for 1X2, over/under 2.5, and BTTS outcomes.

With only ~6 recent matches per team the ratings have high variance.  We
blend each team's observed rate with a global prior weighted at 4 phantom
matches so that teams with no data still get reasonable estimates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .stats import MatchStats


# Global prior rates (international / mixed competition averages)
PRIOR_HOME_GOALS = 1.42
PRIOR_AWAY_GOALS = 1.08
PRIOR_WEIGHT = 4          # phantom match count for Bayesian blending
DC_RHO = -0.13            # Dixon-Coles low-score correlation correction
MAX_GOALS = 9             # captures >99.9% of probability mass


@dataclass(slots=True)
class MatchProbs:
    lambda_home: float
    lambda_away: float
    p1: float          # home win
    px: float          # draw
    p2: float          # away win
    p_over25: float
    p_under25: float
    p_btts: float
    p_no_btts: float

    def edge_1x2(self, bookmaker_implied: tuple[float | None, float | None, float | None]) -> tuple[float | None, float | None, float | None]:
        """Return (edge_home, edge_draw, edge_away) vs bookmaker implied probs."""
        bh, bd, ba = bookmaker_implied
        eh = (self.p1 - bh) if bh else None
        ed = (self.px - bd) if bd else None
        ea = (self.p2 - ba) if ba else None
        return eh, ed, ea


def _poisson_pmf(lam: float, k: int) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _dc_correction(i: int, j: int, lam_h: float, lam_a: float) -> float:
    """Dixon-Coles low-score adjustment to reduce over-prediction of 0-0, 1-0, 0-1, 1-1."""
    if i == 0 and j == 0:
        return 1.0 - DC_RHO * lam_h * lam_a
    if i == 0 and j == 1:
        return 1.0 + DC_RHO * lam_h
    if i == 1 and j == 0:
        return 1.0 + DC_RHO * lam_a
    if i == 1 and j == 1:
        return 1.0 - DC_RHO
    return 1.0


def _blend(observed: float, n_obs: int, prior: float) -> float:
    """Bayesian blending of observed rate with prior."""
    return (n_obs * observed + PRIOR_WEIGHT * prior) / (n_obs + PRIOR_WEIGHT)


def compute_lambdas(match_stats: MatchStats) -> tuple[float, float]:
    """Derive expected goals from team stats using simple Poisson strength model.

    Attack rating = how many goals this team scores on average.
    Defense rating = how many goals this team concedes on average.

    Expected home goals = average of (home attack blended, away defense blended).
    Expected away goals = average of (away attack blended, home defense blended).
    """
    h = match_stats.home
    a = match_stats.away

    h_att = _blend(h.avg_scored, h.n_matches, PRIOR_HOME_GOALS)
    h_def = _blend(h.avg_conceded, h.n_matches, PRIOR_AWAY_GOALS)
    a_att = _blend(a.avg_scored, a.n_matches, PRIOR_AWAY_GOALS)
    a_def = _blend(a.avg_conceded, a.n_matches, PRIOR_HOME_GOALS)

    lam_home = (h_att + a_def) / 2.0
    lam_away = (a_att + h_def) / 2.0

    return max(lam_home, 0.15), max(lam_away, 0.10)


def compute_match_probs(match_stats: MatchStats) -> MatchProbs:
    """Compute full match outcome probabilities using Poisson model."""
    lam_h, lam_a = compute_lambdas(match_stats)

    # Build (MAX_GOALS+1)×(MAX_GOALS+1) score probability matrix
    raw = [
        [_poisson_pmf(lam_h, i) * _poisson_pmf(lam_a, j) * _dc_correction(i, j, lam_h, lam_a)
         for j in range(MAX_GOALS + 1)]
        for i in range(MAX_GOALS + 1)
    ]

    total = sum(raw[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1))

    p1 = sum(raw[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i > j) / total
    px = sum(raw[i][i] for i in range(MAX_GOALS + 1)) / total
    p2 = sum(raw[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if j > i) / total
    p_over = sum(raw[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i + j >= 3) / total
    p_btts = sum(raw[i][j] for i in range(1, MAX_GOALS + 1) for j in range(1, MAX_GOALS + 1)) / total

    return MatchProbs(
        lambda_home=lam_h,
        lambda_away=lam_a,
        p1=p1,
        px=px,
        p2=p2,
        p_over25=p_over,
        p_under25=1.0 - p_over,
        p_btts=p_btts,
        p_no_btts=1.0 - p_btts,
    )
