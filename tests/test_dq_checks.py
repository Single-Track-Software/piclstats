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
