"""Data-quality rollup and check registry (pure)."""

from piclstats.quality.checks import ROW_CHECKS, SEVERITY, rollup_status


def test_rollup_precedence():
    assert rollup_status([]) == "ok"
    assert rollup_status(["info"]) == "ok"
    assert rollup_status(["warn", "info"]) == "warn"
    assert rollup_status(["warn", "error"]) == "excluded"


def test_every_row_check_has_a_severity():
    for check, sev, *_ in ROW_CHECKS:
        assert SEVERITY[check] == sev
        assert sev in ("error", "warn", "info")


def test_try_it_out_rows_are_excluded_and_documented():
    from piclstats.quality.checks import NOT_TRY_IT_OUT, TRY_IT_OUT
    from piclstats.quality.dqpage import CHECK_HELP

    # Single Lap categories are not races: excluded from statistics by their
    # own check, and every other check leaves them alone.
    assert SEVERITY["try_it_out"] == "error"
    assert "Single Lap" in TRY_IT_OUT and TRY_IT_OUT in NOT_TRY_IT_OUT
    assert all(c in CHECK_HELP for c, *_ in ROW_CHECKS)
