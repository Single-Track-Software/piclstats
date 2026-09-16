"""Range checks on admin forecast settings (pure)."""

from piclstats.web.admin import forecast_value_error


def test_in_range_values_pass():
    assert forecast_value_error("recency_decay", 0.8) is None
    assert forecast_value_error("recent_race_count", 5) is None
    assert forecast_value_error("threshold_ready", 60) is None


def test_zero_or_negative_decay_is_refused():
    assert forecast_value_error("recency_decay", -1)
    assert forecast_value_error("recency_decay", 0)


def test_nan_and_inf_are_refused():
    assert forecast_value_error("improvement_weight", float("nan"))
    assert forecast_value_error("reference_climbing_ft_per_mile", float("inf"))


def test_unknown_keys_only_check_finiteness():
    assert forecast_value_error("something_new", 123.0) is None
