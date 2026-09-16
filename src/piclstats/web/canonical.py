"""Canonical-host redirect.

The site answers on piclstats.com, www.piclstats.com and piclstats.fly.dev.
Search engines, shared links and session cookies all work better with one
name, so safe (GET/HEAD) requests on any other host are sent to the host in
PICLSTATS_PUBLIC_BASE_URL with a permanent redirect. Nothing happens when the
base URL is unset (local dev), and non-idempotent requests are left alone so
a form posted from an old bookmark still lands.
"""

from __future__ import annotations

from urllib.parse import urlsplit

SAFE_METHODS = frozenset({"GET", "HEAD"})


def canonical_host(public_base_url: str) -> str:
    """Hostname (no port) from the configured base URL, or '' when unset."""
    if not public_base_url:
        return ""
    return (urlsplit(public_base_url).hostname or "").lower()


def redirect_target(
    method: str, request_host: str, path: str, query: str, public_base_url: str
) -> str | None:
    """Where to send this request, or None to serve it here."""
    canonical = canonical_host(public_base_url)
    if not canonical or method.upper() not in SAFE_METHODS:
        return None
    host = request_host.split(":", 1)[0].lower()
    if not host or host == canonical:
        return None
    base = public_base_url.rstrip("/")
    return f"{base}{path}" + (f"?{query}" if query else "")
