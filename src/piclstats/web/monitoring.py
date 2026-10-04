"""Error reporting to Sentry.

Riders are mostly minors, so nothing identifying goes out: no IPs, cookies,
headers, request bodies or stack-frame variables, and every
event is scrubbed of email addresses and of the secret part of token URLs
(invites, password resets, timing stations) before it leaves the machine.
Log lines are not forwarded; warnings ride along only as breadcrumbs, and
those are scrubbed too.
"""

from __future__ import annotations

import os
import re
from typing import Any

import sentry_sdk

from piclstats.config import settings

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TOKEN_PATH = re.compile(r"(/(?:invite|reset|timing/s)/)[^/?#\s\"']+")


def scrub(value: Any) -> Any:
    """Copy of value with emails and URL tokens masked, recursing into
    dicts and lists."""
    if isinstance(value, str):
        return _TOKEN_PATH.sub(r"\1[token]", _EMAIL.sub("[email]", value))
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub(v) for v in value)
    return value


def _before_send(event: Any, hint: Any) -> Any:
    return scrub(event)


def init() -> bool:
    """Start Sentry if a DSN is configured. Returns whether it did."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=False,
        max_request_body_size="never",
        # Stack-frame locals can hold passwords, emails or session data.
        include_local_variables=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=_before_send,
        before_send_transaction=_before_send,
        environment=os.environ.get("FLY_APP_NAME", "local"),
        release=os.environ.get("FLY_IMAGE_REF"),
    )
    return True
