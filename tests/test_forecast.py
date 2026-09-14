"""Unit tests for the cross-division forecast model (pure — no DB).

This is the analytic PICL leadership singled out, so the properties that must
hold are the directional ones: a faster rider places better, more laps costs
time, the MS→HS loop transition only penalises in that direction, and the
place band always brackets the midpoint.
"""

from __future__ import annotations

import pytest

from piclstats.web.forecast import (
    DEFAULT_CONFIG,
    ForecastInput,
    RaceObservation,
    StatisticalForecastModel,
)


def _obs(pace: float, order: int = 1, elevation: float | None = None, laps: int = 3):
    return RaceObservation(
        event_name=f"Race {order}",
        course_id=order,
        season=2026,
        event_order=order,
        min_per_mile=pace,
        division="MS Beginner",
        loop_type="MS",
        lap_count=laps,
        elevation_ft_per_mile=elevation,
    )


def _inp(observations=None, **overrides):
    kwargs = dict(
        rider_id=1,
        rider_name="Test Rider",
        rider_gender="Male",
        source_division="MS Beginner",
        target_division="MS Intermediate",
        observations=observations if observations is not None else [_obs(8.0), _obs(8.0, 2)],
        # A 20-wide spread of paces so percentile movement is visible.
        target_paces=[6.0 + 0.2 * i for i in range(20)],
        target_field_sizes=[20, 20],
        source_laps=3,
        target_laps=3,
        source_loop_type="MS",
        target_loop_type="MS",
        source_loop_miles=2.0,
        target_loop_miles=2.0,
    )
    kwargs.update(overrides)
    return ForecastInput(**kwargs)


def _model(**config_overrides):
    return StatisticalForecastModel(config=config_overrides or None)


# ── Guard conditions ─────────────────────────────────────────────────


def test_too_few_races_returns_none():
    assert _model().predict(_inp(observations=[_obs(8.0)])) is None


def test_no_target_pace_data_returns_none():
    assert _model().predict(_inp(target_paces=[])) is None


def test_min_races_threshold_is_configurable():
    obs = [_obs(8.0), _obs(8.0, 2), _obs(8.0, 3)]
    assert _model(min_races_for_forecast=4).predict(_inp(observations=obs)) is None
    assert _model(min_races_for_forecast=3).predict(_inp(observations=obs)) is not None


# ── Directional properties ───────────────────────────────────────────


def test_faster_rider_places_better():
    fast = _model().predict(_inp(observations=[_obs(6.2), _obs(6.2, 2)]))
    slow = _model().predict(_inp(observations=[_obs(9.4), _obs(9.4, 2)]))
    assert fast.predicted_place_mid < slow.predicted_place_mid
    assert fast.predicted_percentile > slow.predicted_percentile


def test_extra_laps_slow_the_prediction():
    same = _model().predict(_inp(target_laps=3))
    longer = _model().predict(_inp(target_laps=5))
    assert longer.predicted_min_per_mile > same.predicted_min_per_mile
    assert longer.inputs_summary["fatigue_extra_laps"] == 2


def test_fewer_target_laps_carries_no_bonus():
    # Deliberately conservative: no credit for dropping to a shorter race.
    same = _model().predict(_inp(target_laps=3))
    shorter = _model().predict(_inp(target_laps=1))
    assert shorter.predicted_min_per_mile == same.predicted_min_per_mile
    assert shorter.inputs_summary["fatigue_extra_laps"] == 0


def test_ms_to_hs_loop_transition_penalises():
    same = _model().predict(_inp(source_loop_type="MS", target_loop_type="MS"))
    step_up = _model().predict(_inp(source_loop_type="MS", target_loop_type="HS"))
    assert step_up.predicted_min_per_mile > same.predicted_min_per_mile
    assert step_up.inputs_summary["loop_transition"] == "MS → HS"


def test_hs_to_ms_loop_transition_is_neutral():
    step_down = _model().predict(_inp(source_loop_type="HS", target_loop_type="MS"))
    assert step_down.inputs_summary["loop_transition_factor"] == 1.0


# ── Weighting and trend ──────────────────────────────────────────────


def test_recent_races_outweigh_older_ones():
    improving = [_obs(10.0, 1), _obs(10.0, 2), _obs(7.0, 3)]
    declining = [_obs(7.0, 1), _obs(10.0, 2), _obs(10.0, 3)]
    r_improving = _model().predict(_inp(observations=improving))
    r_declining = _model().predict(_inp(observations=declining))
    # Both have the same mean pace; the one whose *latest* race is fast wins.
    assert r_improving.predicted_min_per_mile < r_declining.predicted_min_per_mile


def test_improving_rider_gets_trend_credit():
    improving = [_obs(9.0, 1), _obs(8.5, 2), _obs(8.0, 3)]
    r = _model().predict(_inp(observations=improving))
    assert r.inputs_summary["improvement_credit"] > 0


def test_declining_rider_gets_no_credit():
    declining = [_obs(8.0, 1), _obs(8.5, 2), _obs(9.0, 3)]
    r = _model().predict(_inp(observations=declining))
    assert r.inputs_summary["improvement_credit"] == 0


def test_trend_credit_needs_three_races():
    two = [_obs(9.0, 1), _obs(8.0, 2)]
    r = _model().predict(_inp(observations=two))
    assert r.inputs_summary["improvement_credit"] == 0


def test_recent_race_count_of_zero_does_not_use_every_race():
    # observations[-0:] is the whole list, so an unclamped 0 would silently mean
    # "use everything" — the opposite of what the admin field reads like.
    obs = [_obs(12.0, 1), _obs(12.0, 2), _obs(6.0, 3)]
    r = _model(recent_race_count=0).predict(_inp(observations=obs))
    assert r.inputs_summary["recent_races_used"] == 1
    assert r.inputs_summary["rider_raw_pace"] == pytest.approx(6.0, abs=0.05)


# ── Course-difficulty normalisation ──────────────────────────────────


def test_hilly_course_normalises_to_a_faster_pace():
    ref = DEFAULT_CONFIG["reference_climbing_ft_per_mile"]
    flat = _model().predict(_inp(observations=[_obs(8.0, 1, ref), _obs(8.0, 2, ref)]))
    hilly = _model().predict(_inp(observations=[_obs(8.0, 1, ref + 100), _obs(8.0, 2, ref + 100)]))
    # Same clock time on a much hillier course means a stronger rider.
    assert hilly.inputs_summary["rider_raw_pace"] < flat.inputs_summary["rider_raw_pace"]
    assert hilly.inputs_summary["difficulty_normalized"] is True


def test_missing_elevation_data_is_reported_not_guessed():
    r = _model().predict(_inp(observations=[_obs(8.0), _obs(8.0, 2)]))
    assert r.inputs_summary["difficulty_normalized"] is False
    assert r.inputs_summary["races_with_elevation_data"] == 0
    assert r.inputs_summary["avg_difficulty_adjustment"] == 1.0


# ── Place band and field scaling ─────────────────────────────────────


def test_place_band_brackets_the_midpoint():
    r = _model().predict(_inp(observations=[_obs(7.0), _obs(9.0, 2)]))
    assert r.predicted_place_low <= r.predicted_place_mid <= r.predicted_place_high


def test_places_stay_within_the_typical_field():
    for pace in (3.0, 8.0, 30.0):
        r = _model().predict(_inp(observations=[_obs(pace), _obs(pace, 2)]))
        assert 1 <= r.predicted_place_low <= r.typical_field_size
        assert 1 <= r.predicted_place_high <= r.typical_field_size


def test_place_scales_to_typical_field_not_pool_size():
    # 200 pace samples pooled across events, but a real race is ~30 riders.
    big_pool = [6.0 + 0.02 * i for i in range(200)]
    r = _model().predict(
        _inp(
            observations=[_obs(8.0), _obs(8.0, 2)],
            target_paces=big_pool,
            target_field_sizes=[30, 30],
        )
    )
    assert r.typical_field_size == 30
    assert r.predicted_place_mid <= 30
    assert r.inputs_summary["target_sample_size"] == 200


def test_fastest_possible_rider_takes_first():
    r = _model().predict(_inp(observations=[_obs(2.0), _obs(2.0, 2)]))
    assert r.predicted_place_mid == 1
    assert r.predicted_percentile == 100.0


# ── Labels ───────────────────────────────────────────────────────────


def test_readiness_tracks_percentile():
    fast = _model().predict(_inp(observations=[_obs(6.0), _obs(6.0, 2)]))
    mid = _model().predict(_inp(observations=[_obs(8.6), _obs(8.6, 2)]))
    slow = _model().predict(_inp(observations=[_obs(11.0), _obs(11.0, 2)]))
    assert fast.readiness == "Ready"
    assert mid.readiness == "Competitive"
    assert slow.readiness == "Developing"
    assert (fast.readiness_color, slow.readiness_color) == ("green", "red")


def test_readiness_thresholds_are_configurable():
    obs = [_obs(8.6), _obs(8.6, 2)]
    strict = _model(readiness_thresholds={"ready": 90, "competitive": 80})
    assert strict.predict(_inp(observations=obs)).readiness == "Developing"


def test_confidence_reflects_sample_depth():
    five = [_obs(8.0, i) for i in range(1, 6)]
    three = [_obs(8.0, i) for i in range(1, 4)]
    two = [_obs(8.0), _obs(8.0, 2)]
    deep_pool = [6.0 + 0.1 * i for i in range(25)]
    thin_pool = [6.0 + 0.5 * i for i in range(12)]

    assert _model().predict(_inp(observations=five, target_paces=deep_pool)).confidence == "High"
    assert _model().predict(_inp(observations=three, target_paces=thin_pool)).confidence == "Medium"
    assert _model().predict(_inp(observations=two, target_paces=[6.0, 7.0])).confidence == "Low"


def test_summary_reports_the_knobs_that_were_applied():
    r = _model().predict(_inp(target_laps=5, source_loop_type="MS", target_loop_type="HS"))
    s = r.inputs_summary
    assert s["source_division"] == "MS Beginner"
    assert s["target_division"] == "MS Intermediate"
    assert s["fatigue_pct"] == pytest.approx(6.0)
    assert s["loop_transition_factor"] == DEFAULT_CONFIG["ms_to_hs_loop_penalty"]
    assert s["typical_field_size"] == 20
