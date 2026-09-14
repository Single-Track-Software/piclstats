"""Unit tests for the login throttle (clock injected — no sleeping)."""

from __future__ import annotations

from piclstats.web import ratelimit


class _Clock:
    """Manually advanced stand-in for time.monotonic."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _throttle(clock, **kwargs):
    return ratelimit.LoginThrottle(
        max_attempts=kwargs.get("max_attempts", 3),
        window_seconds=kwargs.get("window_seconds", 600),
        lockout_seconds=kwargs.get("lockout_seconds", 900),
        clock=clock,
    )


def test_allows_attempts_below_the_limit():
    t = _throttle(_Clock())
    t.record_failure("k")
    t.record_failure("k")
    assert t.retry_after("k") == 0


def test_locks_out_at_the_limit():
    clock = _Clock()
    t = _throttle(clock)
    for _ in range(3):
        t.record_failure("k")
    assert t.retry_after("k") == 900


def test_lockout_expires():
    clock = _Clock()
    t = _throttle(clock)
    for _ in range(3):
        t.record_failure("k")
    clock.advance(899)
    assert t.retry_after("k") == 1
    clock.advance(2)
    assert t.retry_after("k") == 0


def test_failures_outside_the_window_do_not_accumulate():
    # Three failures spread over hours must not add up to a lockout.
    clock = _Clock()
    t = _throttle(clock)
    for _ in range(2):
        t.record_failure("k")
    clock.advance(601)
    t.record_failure("k")
    assert t.retry_after("k") == 0


def test_success_clears_history():
    clock = _Clock()
    t = _throttle(clock)
    t.record_failure("k")
    t.record_failure("k")
    t.record_success("k")
    t.record_failure("k")
    assert t.retry_after("k") == 0


def test_lockout_is_per_key():
    clock = _Clock()
    t = _throttle(clock)
    for _ in range(3):
        t.record_failure("a")
    assert t.retry_after("a") > 0
    assert t.retry_after("b") == 0


def test_key_pairs_ip_with_account():
    # Keyed on both, so an attacker hammering one account from one host cannot
    # lock the real coach out from their own IP.
    attacker = ratelimit.client_key("203.0.113.9", "coach@example.com")
    coach = ratelimit.client_key("198.51.100.4", "coach@example.com")
    assert attacker != coach


def test_key_normalizes_email_case_and_space():
    assert ratelimit.client_key("1.2.3.4", "  Coach@Example.COM ") == ratelimit.client_key(
        "1.2.3.4", "coach@example.com"
    )


def test_missing_ip_still_produces_a_key():
    assert ratelimit.client_key(None, "a@b.c").startswith("unknown|")


def test_buckets_are_pruned_once_stale():
    clock = _Clock()
    t = _throttle(clock)
    t.record_failure("old")
    clock.advance(601)
    t.record_failure("new")  # triggers a prune
    assert "old" not in t._buckets
    assert "new" in t._buckets


def test_locked_bucket_survives_pruning():
    clock = _Clock()
    t = _throttle(clock)
    for _ in range(3):
        t.record_failure("locked")
    clock.advance(601)
    t.record_failure("other")  # prune runs while 'locked' is still serving time
    assert t.retry_after("locked") > 0
