"""CSV cells and download names (pure)."""

from piclstats.web.exports import csv_safe, csv_safe_row, filename_part


def test_formula_leaders_are_neutralised_and_everything_else_passes():
    assert csv_safe('=HYPERLINK("http://x")') == '\'=HYPERLINK("http://x")'
    assert csv_safe("+1") == "'+1" and csv_safe("-2") == "'-2" and csv_safe("@x") == "'@x"
    assert csv_safe("\tfoo") == "'\tfoo"
    assert csv_safe("Alice Smith") == "Alice Smith"
    assert csv_safe(12) == 12 and csv_safe(None) is None and csv_safe(-0.5) == -0.5
    assert csv_safe_row(["=a", 1, None]) == ["'=a", 1, None]


def test_filename_part_keeps_only_safe_characters():
    assert filename_part("Eastern Blue") == "Eastern_Blue"
    assert filename_part('x"; rm -rf /') == "x_rm_-rf"
    assert filename_part("") == "x"
    assert len(filename_part("a" * 100)) == 40
