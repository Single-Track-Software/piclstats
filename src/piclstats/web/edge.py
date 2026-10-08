"""Cloudflare in front of Fly: lock the origin and trust only Cloudflare's IP header.

Cloudflare proxies piclstats.com and adds a secret header to every request
it forwards (a Transform Rule; the same value is the Fly secret
PICLSTATS_ORIGIN_SECRET). With the secret set:

- any request without the header is refused, so scrapers can't go around
  Cloudflare by calling piclstats.fly.dev or Fly's IP directly. /healthz
  stays open for Fly's own health check (it reveals nothing);
- the caller's IP comes from CF-Connecting-IP, since Fly-Client-IP is now a
  Cloudflare edge address. Only header-verified requests may set it.

Unset (local dev, tests, before cutover) nothing changes.
"""

from __future__ import annotations

import hmac

from fastapi import Request

from piclstats.config import settings

ORIGIN_HEADER = "x-origin-auth"
OPEN_PATHS = frozenset({"/healthz"})
# The keyed API is called directly on Fly (Cloudflare's Bot Fight Mode
# challenges every automated client); web/api.py's key check guards it.
OPEN_PREFIXES = ("/api/v1/",)


def lock_enabled() -> bool:
    return bool(settings.origin_secret)


def via_cloudflare(request: Request) -> bool:
    """True when the request carries Cloudflare's origin secret."""
    secret = settings.origin_secret
    given = request.headers.get(ORIGIN_HEADER, "")
    return bool(secret) and hmac.compare_digest(given.encode(), secret.encode())


def allowed(request: Request) -> bool:
    """Whether the origin lock lets this request through."""
    path = request.url.path
    return (
        not lock_enabled()
        or path in OPEN_PATHS
        or path.startswith(OPEN_PREFIXES)
        or via_cloudflare(request)
    )


def client_ip(request: Request) -> str | None:
    """Caller's IP: Cloudflare's view when the request came through it, else Fly's.

    On Fly the socket peer is always the edge proxy, so the proxy header is
    what identifies the caller; no X-Forwarded-For fallback, since off Fly
    there is no trusted proxy and the throttle key must not be caller-chosen.
    """
    if via_cloudflare(request):
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip
    fly_ip = request.headers.get("fly-client-ip")
    if fly_ip:
        return fly_ip
    return request.client.host if request.client else None
