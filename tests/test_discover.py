"""Parsing race links off the league results page (pure)."""

from datetime import date

from piclstats.scraper.discover import current_season, parse_links

PAGE = """
<p><a target="_blank" href="https://my.raceresult.com/410562/">State: Playin’ at Penn College</a></p>
<p><a href="https://my.raceresult.com/410562/">duplicate of the same race</a></p>
<p><a href="http://my.raceresult.com/411000">  Grinnin’ at   Granite </a></p>
<a href="https://my.raceresult.com/nope/">not an id</a>
"""


def test_parse_links_dedupes_and_cleans_names():
    found = parse_links(PAGE)
    assert [(d.raceresult_id, d.name) for d in found] == [
        (410562, "State: Playin’ at Penn College"),
        (411000, "Grinnin’ at Granite"),
    ]


def test_season_is_the_calendar_year():
    assert current_season(date(2026, 9, 16)) == 2026
