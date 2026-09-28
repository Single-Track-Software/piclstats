"""Event registry lookups (pure)."""

from piclstats.scraper.registry import SEASONS, get_events, lookup_event


def test_lookup_matches_get_events():
    for season, order, eid in get_events():
        assert lookup_event(eid) == (season, order)


def test_unknown_id_is_none():
    assert lookup_event(1) is None
    assert 1 not in {e for ids in SEASONS.values() for e in ids}


def test_canonical_cte_leaves_hidden_riders_out():
    from piclstats.web.queries import _CANONICAL_CTE

    assert "WHERE NOT ri.hidden" in _CANONICAL_CTE
