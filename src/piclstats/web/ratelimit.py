"""In-process login throttle.

Without this, /login accepts unlimited password guesses. Coach accounts are
handed out by an admin and tend to have human-chosen passwords, so an unbounded
guess rate is the cheapest way in.

Scope: one process. The app runs as a single Fly machine, so a shared store
would be premature — but that is the assumption to revisit before scaling past
one machine, because each machine would then keep its own counter.

Pure and clock-injectable so the behaviour is testable without sleeping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# Roughly "a wrong password twice, plus fat fingers" before a cooldown, and a
# cooldown short enough that a locked-out coach can just wait it out.
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60


@dataclass
class _Bucket:
    count: int = 0
    window_start: float = 0.0
    locked_until: float = 0.0


@dataclass
class LoginThrottle:
    max_attempts: int = MAX_ATTEMPTS
    window_seconds: float = WINDOW_SECONDS
    lockout_seconds: float = LOCKOUT_SECONDS
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again; 0 when it is not locked out."""
        bucket = self._buckets.get(key)
        if bucket is None:
            return 0
        remaining = bucket.locked_until - self.clock()
        return max(0, int(remaining + 0.999)) if remaining > 0 else 0

    def record_failure(self, key: str) -> None:
        now = self.clock()
        bucket = self._buckets.setdefault(key, _Bucket(window_start=now))
        # Failures older than the window don't count toward a lockout.
        if now - bucket.window_start > self.window_seconds:
            bucket.count = 0
            bucket.window_start = now
        bucket.count += 1
        if bucket.count >= self.max_attempts:
            bucket.locked_until = now + self.lockout_seconds
            bucket.count = 0
            bucket.window_start = now
        self._prune(now)

    def record_success(self, key: str) -> None:
        """Clear history for a key that just authenticated."""
        self._buckets.pop(key, None)

    def _prune(self, now: float) -> None:
        # Keep the dict from growing without bound under a spray of distinct
        # emails: drop buckets that are neither locked nor inside their window.
        stale = [
            k
            for k, b in self._buckets.items()
            if b.locked_until <= now and now - b.window_start > self.window_seconds
        ]
        for k in stale:
            del self._buckets[k]


def client_key(client_ip: str | None, email: str) -> str:
    """Throttle key: source IP plus target account.

    Keyed on both so one attacker cannot lock every coach out of their own
    account by guessing at it (the pair is what gets locked, not the account),
    while still slowing a single host working through a password list.
    """
    return f"{client_ip or 'unknown'}|{email.strip().lower()}"
