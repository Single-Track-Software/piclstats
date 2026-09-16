"""Place/status parsing from raceresult rows (pure)."""

from piclstats.scraper.parser import parse_place_status


def test_positive_place_is_ok():
    assert parse_place_status(" 12 ") == (12, "OK")


def test_star_and_blank_are_not_ranked():
    assert parse_place_status("*") == (None, "NR")
    assert parse_place_status("") == (None, "NR")


def test_nonpositive_place_is_not_ranked():
    assert parse_place_status("-1") == (None, "NR")
    assert parse_place_status("0") == (None, "NR")


def test_status_words_pass_through_upper():
    assert parse_place_status("dnf") == (None, "DNF")
