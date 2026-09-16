"""Season query-string parsing (pure)."""

import pytest

from piclstats.web.app import parse_season


@pytest.mark.parametrize("raw", ["", None, "  ", "abc", "2025-26", "2025.0", "25", "20250"])
def test_non_years_mean_all_seasons(raw):
    assert parse_season(raw) is None


def test_year_parses():
    assert parse_season("2026") == 2026
    assert parse_season(" 2024 ") == 2024
