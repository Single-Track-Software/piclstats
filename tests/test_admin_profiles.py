"""Parsing of the per-season course profile form (admin)."""

import pytest

from piclstats.db.seed import PROFILE_KEYS
from piclstats.web.admin import parse_profile_form


def _lap_key(division: str, gender: str | None) -> str:
    return f"lap_{PROFILE_KEYS.index((division, gender))}"


def test_blank_fields_parse_as_none():
    parsed = parse_profile_form({})
    assert parsed.loops == {"MS": (None, None), "HS": (None, None)}
    assert all(v is None for v in parsed.laps.values())
    assert len(parsed.laps) == len(PROFILE_KEYS)


def test_loop_and_lap_values_round_trip():
    parsed = parse_profile_form(
        {
            "ms_distance_miles": "2.1",
            "ms_elevation_ft": "245",
            "hs_distance_miles": " 3.4 ",
            _lap_key("Varsity", "Male"): "3",
            _lap_key("Single Lap High School", None): "1",
        }
    )
    assert parsed.loops["MS"] == (2.1, 245.0)
    assert parsed.loops["HS"] == (3.4, None)
    assert parsed.laps[PROFILE_KEYS.index(("Varsity", "Male"))] == 3
    assert parsed.laps[PROFILE_KEYS.index(("Single Lap High School", None))] == 1
    assert parsed.laps[PROFILE_KEYS.index(("JV1", "Female"))] is None


@pytest.mark.parametrize("bad", ["0", "7", "abc", "2.5"])
def test_lap_count_must_be_small_integer(bad):
    with pytest.raises(ValueError):
        parse_profile_form({_lap_key("JV2", "Male"): bad})


def test_loop_distance_must_be_numeric():
    with pytest.raises(ValueError):
        parse_profile_form({"hs_distance_miles": "three"})


def test_profile_keys_exclude_folded_alias():
    # Results are normalized to "MS Advanced", so the alias must not get its own row.
    assert ("Middle School Advanced", "Male") not in PROFILE_KEYS
    assert ("MS Advanced", "Male") in PROFILE_KEYS
