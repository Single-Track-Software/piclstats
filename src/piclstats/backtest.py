"""Replay past seasons through the future-race forecast (`ratings.py`).

For every points race after a season's opener, rebuild what the forecast page
would have known the night before — ratings from earlier races only, the
season's roster so far, the race's conference — forecast every rostered rider
who then started, and compare with where they actually finished.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean, median

from piclstats.web.ratings import (
    RATING_CONFIG,
    _squash,
    build_roster,
    expected_field_size,
    field_history,
    place_distribution,
    race_scores,
)


@dataclass
class BacktestResult:
    """Errors are in share-of-field points (finishing 30th of 60 when forecast
    40th of 80 is a hit): the forecast can't know how many will start."""

    fields: int = 0
    forecasts: int = 0
    errors: list[float] = field(default_factory=list)
    place_errors: list[int] = field(default_factory=list)
    in_range: int = 0  # actual place inside the 80% range as shown (in places)
    in_range_share: int = 0  # same, judged on share of field
    # On the riders who have a previous race: model vs "same share as last time".
    versus: dict[str, dict[str, list[float]]] = field(
        default_factory=lambda: defaultdict(lambda: {"model": [], "naive": []})
    )

    def merge(self, other: "BacktestResult") -> None:
        self.fields += other.fields
        self.forecasts += other.forecasts
        self.errors += other.errors
        self.place_errors += other.place_errors
        self.in_range += other.in_range
        self.in_range_share += other.in_range_share
        for move, pair in other.versus.items():
            self.versus[move]["model"] += pair["model"]
            self.versus[move]["naive"] += pair["naive"]

    def report(self) -> str:
        if not self.forecasts:
            return "No forecasts to score."
        lines = [
            f"{self.forecasts} forecasts across {self.fields} division fields",
            f"  mean error            {mean(self.errors):5.1f} points of field share",
            f"  median error          {median(self.place_errors):5.0f} places",
            f"  inside the 80% range  {self.in_range / self.forecasts:5.0%} by place, "
            f"{self.in_range_share / self.forecasts:.0%} by share of field",
            "",
            f"  {'previous race -> this race':34}{'n':>6}{'model':>8}{'same-as-last':>14}",
        ]
        everything: dict[str, list[float]] = {"model": [], "naive": []}
        for move in sorted(self.versus):
            pair = self.versus[move]
            everything["model"] += pair["model"]
            everything["naive"] += pair["naive"]
            lines.append(
                f"  {move:34}{len(pair['model']):>6}{mean(pair['model']):>8.1f}{mean(pair['naive']):>14.1f}"
            )
        lines.append(
            f"  {'all':34}{len(everything['model']):>6}"
            f"{mean(everything['model']):>8.1f}{mean(everything['naive']):>14.1f}"
        )
        return "\n".join(lines)


def run_backtest(
    rows: list[dict], min_season: int = 2024, config: dict | None = None
) -> BacktestResult:
    """`rows` = queries.rating_rows() for one gender + loop, oldest first."""
    cfg = {**RATING_CONFIG, **(config or {})}
    scores = race_scores(rows, cfg)
    all_fields = field_history(rows)
    events: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        events[r["event_id"]].append(r)
    ordered = sorted(
        events, key=lambda e: (events[e][0]["season"], events[e][0]["event_order"] or 0, e)
    )

    out = BacktestResult()
    last_share: dict[int, tuple] = {}  # rider -> (percentile, division, was conference race)
    for event_id in ordered:
        results = events[event_id]
        season, order = results[0]["season"], results[0]["event_order"] or 0
        prior = [r for r in rows if (r["season"], r["event_order"] or 0) < (season, order)]
        conferences = {_squash(r["conference"]) for r in results if r.get("conference")}
        conference = conferences.pop() if len(conferences) == 1 else None

        by_div: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            by_div[r["division"]].append(r)

        if season >= min_season and any(r["season"] == season for r in prior):
            roster = build_roster(prior, scores, season, (season, order), cfg)
            roster_by_id = {e["rider_id"]: e for e in roster}
            for division, starters in by_div.items():
                pool = [
                    e
                    for e in roster
                    if e["division"] == division
                    and (conference is None or e["conference"] == conference)
                ]
                if len(pool) < 10 or len(starters) < 10:
                    continue
                out.fields += 1
                actual_field = len(starters)
                expected = expected_field_size(
                    [h for h in all_fields if (h["season"], h["event_order"]) < (season, order)],
                    season,
                    division,
                    conference,
                )
                for r in starters:
                    me = roster_by_id.get(r["rider_id"])
                    if me is None or me["division"] != division or me["rating"].races == 0:
                        continue
                    others = [e["rating"] for e in pool if e["rider_id"] != r["rider_id"]]
                    cell = place_distribution(me["rating"], others, 0.0, expected)
                    span = max(cell["field"] - 1, 1)
                    share = (cell["place"] - 1) / span
                    actual_share = (r["place"] - 1) / max(actual_field - 1, 1)
                    error = abs(share - actual_share) * 100
                    out.forecasts += 1
                    out.errors.append(error)
                    out.place_errors.append(abs(cell["place"] - r["place"]))
                    out.in_range += cell["place_low"] <= r["place"] <= cell["place_high"]
                    out.in_range_share += (
                        (cell["place_low"] - 1.5) / span
                        <= actual_share
                        <= (cell["place_high"] - 0.5) / span
                    )
                    if r["rider_id"] in last_share:
                        previous, last_div, last_conf = last_share[r["rider_id"]]
                        move = (
                            ("conference" if last_conf else "state")
                            + " -> "
                            + ("conference" if conference else "state")
                        )
                        out.versus[move]["model"].append(error)
                        out.versus[move]["naive"].append(abs(previous - actual_share) * 100)

        for starters in by_div.values():
            n = len(starters)
            for r in starters:
                last_share[r["rider_id"]] = (
                    (r["place"] - 1) / max(n - 1, 1),
                    r["division"],
                    conference is not None,
                )
    return out
