"""Extract team form features from statisticsv2.iddaa.com card-corners data.

card-corners returns the last 6 matches for BOTH teams (h and a) of a
future event.  Each past match includes goals for each side, plus corners
and cards — enough to build basic attack / defense strength ratings.

Result codes: "G" = win for the queried team, "B" = draw, "M" = loss.
(We derive win/draw/loss ourselves from scores rather than relying on mr.)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TeamStats:
    name: str
    n_matches: int
    avg_scored: float
    avg_conceded: float
    wins: int
    draws: int
    losses: int
    avg_corners: float | None
    avg_yellow_cards: float | None

    @property
    def form_string(self) -> str:
        """W/D/L counts as a compact string."""
        return f"W{self.wins}D{self.draws}L{self.losses}"


@dataclass(slots=True)
class MatchStats:
    event_id: int
    home: TeamStats
    away: TeamStats
    has_data: bool = True


def _team_stats_from_matches(team_name: str, matches: list[dict]) -> TeamStats | None:
    """Build TeamStats from card-corners match list for a given team.

    Each entry in `matches` has structure:
        {"h": {"n": name, "s": goals, "c": corners, "yc": yellows, "rc": reds},
         "a": {"n": name, "s": goals, ...}, "t": timestamp, "ln": league_name}
    """
    if not matches:
        return None

    goals_scored: list[int] = []
    goals_conceded: list[int] = []
    corners: list[int] = []
    yellow_cards: list[int] = []
    wins = draws = losses = 0

    for m in matches:
        h = m.get("h", {})
        a = m.get("a", {})
        h_name = h.get("n", "")
        a_name = a.get("n", "")
        h_goals = h.get("s")
        a_goals = a.get("s")

        if h_goals is None or a_goals is None:
            continue

        if h_name == team_name:
            scored = int(h_goals)
            conceded = int(a_goals)
            team_side = h
        elif a_name == team_name:
            scored = int(a_goals)
            conceded = int(h_goals)
            team_side = a
        else:
            # Sometimes team name differs slightly; try partial match
            if team_name and (team_name[:4].lower() in h_name.lower()):
                scored = int(h_goals)
                conceded = int(a_goals)
                team_side = h
            elif team_name and (team_name[:4].lower() in a_name.lower()):
                scored = int(a_goals)
                conceded = int(h_goals)
                team_side = a
            else:
                continue

        goals_scored.append(scored)
        goals_conceded.append(conceded)

        c = team_side.get("c")
        if c is not None:
            corners.append(int(c))
        yc = team_side.get("yc")
        if yc is not None:
            yellow_cards.append(int(yc))

        if scored > conceded:
            wins += 1
        elif scored == conceded:
            draws += 1
        else:
            losses += 1

    n = len(goals_scored)
    if n == 0:
        return None

    return TeamStats(
        name=team_name,
        n_matches=n,
        avg_scored=sum(goals_scored) / n,
        avg_conceded=sum(goals_conceded) / n,
        wins=wins,
        draws=draws,
        losses=losses,
        avg_corners=sum(corners) / len(corners) if corners else None,
        avg_yellow_cards=sum(yellow_cards) / len(yellow_cards) if yellow_cards else None,
    )


def extract_match_stats(event_id: int, card_corners_data: dict) -> MatchStats:
    """Parse card-corners API response into MatchStats."""
    h_data = card_corners_data.get("h", {})
    a_data = card_corners_data.get("a", {})
    h_name = h_data.get("n", "")
    a_name = a_data.get("n", "")
    h_matches = h_data.get("m", [])
    a_matches = a_data.get("m", [])

    home_stats = _team_stats_from_matches(h_name, h_matches)
    away_stats = _team_stats_from_matches(a_name, a_matches)

    if home_stats is None or away_stats is None:
        return MatchStats(event_id=event_id, home=_fallback(h_name), away=_fallback(a_name), has_data=False)

    return MatchStats(event_id=event_id, home=home_stats, away=away_stats)


def _fallback(name: str) -> TeamStats:
    return TeamStats(
        name=name,
        n_matches=0,
        avg_scored=1.25,
        avg_conceded=1.25,
        wins=0,
        draws=0,
        losses=0,
        avg_corners=None,
        avg_yellow_cards=None,
    )
