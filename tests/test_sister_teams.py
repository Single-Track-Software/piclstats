"""Sister teams: one club split into NICA teams, shown as one roster."""

from __future__ import annotations

from fastapi.testclient import TestClient

from piclstats.web import app as app_mod
from piclstats.web import queries
from piclstats.web.app import app, family_context, sister_teams
from piclstats.web.queries import _team_keys


def test_team_keys_normalise_and_drop_duplicates_and_self():
    keys = _team_keys(
        "Lower Bucks  Pennsbury", ["lower bucks composite", "Lower Bucks Pennsbury", ""]
    )
    assert keys == ["lower bucks pennsbury", "lower bucks composite"]


def test_team_keys_without_sisters_is_the_team_alone():
    assert _team_keys("Lower Bucks Composite") == ["lower bucks composite"]


def _display(monkeypatch, known: dict[str, str]):
    monkeypatch.setattr(
        queries, "team_display_name", lambda session, name: known.get(name.strip().lower())
    )


def test_sister_teams_keeps_only_real_teams_in_order(monkeypatch):
    _display(
        monkeypatch,
        {
            "lower bucks composite": "Lower Bucks Composite",
            "twin valley school": "Twin Valley School",
        },
    )
    got = sister_teams(
        None,
        "Lower Bucks Pennsbury",
        [
            " lower bucks composite ",
            "No Such Team",
            "Lower Bucks Pennsbury",
            "twin valley school",
            "lower bucks composite",
        ],
    )
    assert got == ["Lower Bucks Composite", "Twin Valley School"]


def test_sister_teams_are_capped(monkeypatch):
    _display(monkeypatch, {f"team {i}": f"Team {i}" for i in range(10)})
    got = sister_teams(None, "Home", [f"team {i}" for i in range(10)])
    assert len(got) == app_mod.MAX_SISTER_TEAMS


def test_family_context_builds_links_and_picker(monkeypatch):
    monkeypatch.setattr(
        queries,
        "teams_list",
        lambda session: ["A", "Lower Bucks Composite", "Lower Bucks Pennsbury", "Z"],
    )
    ctx = family_context(None, "Lower Bucks Pennsbury", ["Lower Bucks Composite"])
    assert ctx["family_name"] == "Lower Bucks Pennsbury + Lower Bucks Composite"
    assert ctx["with_query"] == "&with=Lower%20Bucks%20Composite"
    assert ctx["addable_teams"] == ["A", "Z"]


def test_family_context_without_sisters(monkeypatch):
    monkeypatch.setattr(queries, "teams_list", lambda session: ["A", "Home"])
    ctx = family_context(None, "Home", [])
    assert ctx["family_name"] == "Home"
    assert ctx["with_query"] == ""
    assert ctx["addable_teams"] == ["A"]


def test_recap_route_passes_sisters_to_every_query(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(app_mod, "get_session", lambda: _NullSession())
    _display(monkeypatch, {"lower bucks composite": "Lower Bucks Composite"})
    monkeypatch.setattr(queries, "teams_list", lambda session: [])

    def seasons(session, team, sisters=()):
        seen["seasons"] = list(sisters)
        return [2025, 2026]

    def rows(session, team, season, sisters=()):
        seen["rows"] = (season, list(sisters))
        return []

    def movers(session, team, season, sisters=()):
        seen["movers"] = list(sisters)
        return []

    monkeypatch.setattr(queries, "team_seasons", seasons)
    monkeypatch.setattr(queries, "team_season_rows", rows)
    monkeypatch.setattr(queries, "team_rider_seasons", movers)
    r = TestClient(app).get(
        "/team/Lower Bucks Pennsbury/recap",
        params={"season": "2026", "with": ["Lower Bucks Composite", "Nope"]},
    )
    assert r.status_code == 200
    assert seen == {
        "seasons": ["Lower Bucks Composite"],
        "rows": (2026, ["Lower Bucks Composite"]),
        "movers": ["Lower Bucks Composite"],
    }
    assert "Lower Bucks Pennsbury + Lower Bucks Composite" in r.text
    assert "with=Lower%20Bucks%20Composite" in r.text  # season pills keep the family


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
