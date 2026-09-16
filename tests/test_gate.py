"""Publish-gate rules (pure)."""

from piclstats.quality.scorecard import evaluate_gate


def test_clean_run_passes():
    ok, reasons = evaluate_gate(
        {"golden_overall_pass_pct": 100.0, "golden_negative_pairs_pass_pct": 100.0}, None
    )
    assert ok and reasons == []


def test_no_fixtures_yet_is_not_a_failure():
    ok, _ = evaluate_gate(
        {"golden_overall_pass_pct": None, "golden_negative_pairs_pass_pct": None}, None
    )
    assert ok


def test_over_merge_blocks():
    ok, reasons = evaluate_gate({"golden_negative_pairs_pass_pct": 66.67}, None)
    assert not ok and "negative golden pairs" in reasons[0]


def test_contamination_must_not_increase():
    ok, reasons = evaluate_gate({"timestamp_totals": 196}, {"timestamp_totals": 195})
    assert not ok and "timestamp_totals increased 195 -> 196" in reasons
    ok, _ = evaluate_gate({"timestamp_totals": 195}, {"timestamp_totals": 195})
    assert ok


def test_bad_event_and_count_drop_block():
    ok, reasons = evaluate_gate({"event_excluded_pct": 12.5, "event_results_count_drop": 1}, None)
    assert not ok and len(reasons) == 2


def test_golden_regression_blocks():
    ok, reasons = evaluate_gate(
        {"golden_overall_pass_pct": 95.0}, {"golden_overall_pass_pct": 98.0}
    )
    assert not ok and "regressed" in reasons[0]
