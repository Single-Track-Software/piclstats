"""Merge-mechanism classification (pure)."""

from piclstats.quality.lineage import classify_mechanism


def test_cheapest_explanation_wins():
    assert classify_mechanism("NEVE OREILLY", "NEVE OREILLY") == ("exact", 1.0)
    assert classify_mechanism("Neve OReilly", "NEVE OREILLY") == ("casing", 1.0)
    assert classify_mechanism("NEVE  OREILLY", "NEVE OREILLY") == ("whitespace", 1.0)
    assert classify_mechanism("NEVE O'REILLY", "NEVE OREILLY") == ("punctuation", 1.0)
    assert classify_mechanism("GALEN LA LONDE", "GALEN LALONDE") == ("punctuation", 1.0)


def test_typo_carries_a_score():
    mech, score = classify_mechanism("JON SMITH", "JOHN SMITH")
    assert mech == "typo"
    assert 0.8 < score < 1.0
