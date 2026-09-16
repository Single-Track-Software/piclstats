"""Rider and team blocking keys (pure)."""

from piclstats.quality.keys import name_key, team_key


def test_punctuation_and_spacing_variants_share_a_key():
    assert name_key("NEVE O'REILLY") == name_key("NEVE OREILLY") == "NEVEOREILLY"
    assert name_key("GALEN LA LONDE") == name_key("GALEN LALONDE")
    assert name_key("Mary-Kate Smith") == "MARYKATESMITH"


def test_accents_fold():
    assert name_key("José Núñez") == "JOSENUNEZ"


def test_team_key_collapses_case_and_whitespace():
    assert team_key("PGH  north ") == team_key("Pgh North") == "pgh north"
    assert team_key(None) is None
    assert team_key("   ") is None
