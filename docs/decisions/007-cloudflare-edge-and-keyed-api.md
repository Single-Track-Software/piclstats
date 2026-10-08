# 007 — Cloudflare at the edge, origin locked; a keyed API on the origin

**Date:** 2026-10-08  
**Status:** Accepted (Chris). Edge live 2026-10-07; API in PRs #110 and #111.

## Context

From 28 Sep to 5 Oct a crawler harvested the public rider pages:
- It sent normal browser user-agents and spoofed `www.google.com` referrers, and rotated IPs.
- Traffic ran flat around the clock, up to 48 pages/min, peaking at 9,935 "Google" views on 3 Oct.
- It hit 2,356 of the 2,748 rider pages near-uniformly, then stopped on 6 Oct.

The app's user-agent bot filter counted all of it as human. Rider pages carry minors' names, and Chris wants to resist harvesting while keeping rider pages in the sitemap for search.

At the same time Chris is building a Team Director agent (Grokbot) that needs race results as data for a weekly website post and a parent/rider email.

## Decision

1. **Cloudflare (free plan) proxies piclstats.com.**
   - Bot Fight Mode on; AI training crawlers blocked (search and agent bots allowed); Bot Preference Sync writes the same policy into robots.txt.
   - A rate-limit rule on `/rider/`.
   - SSL Full (strict). Fly keeps its Let's Encrypt certificate, renewed via DNS: the `_acme-challenge` CNAMEs plus `_fly-ownership` TXT records. Fly requires the TXT behind a proxy.
2. **The origin only serves Cloudflare.** A Transform Rule sends a secret `X-Origin-Auth`, and the app refuses requests without it (`web/edge.py`, Fly secret `PICLSTATS_ORIGIN_SECRET`).
   - `/healthz` stays open for Fly's checks and for UptimeRobot (which now monitors the fly.dev URL).
   - The visitor IP comes from `CF-Connecting-IP`, trusted only on header-verified requests.
3. **Automated partners use a keyed API on the origin**, `piclstats.fly.dev/api/v1`, not piclstats.com.
   - Bot Fight Mode challenges every automated client, and the free plan can't exempt one. The origin lock lets `/api/v1/` through, and the key check decides.
   - Keys are SHA-256 hashed, shown once, scoped to named teams, rate limited (60/min) and revocable in admin.
   - There are no league-wide bulk endpoints.
4. **Terms of use** (`/terms`) forbid automated collection and dataset/AI use, so "ask first" has an answer: a key.
5. FastAPI's public `/docs`, `/redoc` and `/openapi.json` are off. The API is documented in `docs/api.md`.

## Consequences

- Command-line tools (curl, httpx) get Cloudflare's challenge on piclstats.com. Checks run from a real browser, `fly machine exec`, or the fly.dev `/healthz`.
- Cloudflare caches `.txt`, `.xml` and `.css` at the edge (up to 4 h). HTML is not cached.
- A rotating-proxy crawler is stopped by Bot Fight Mode, not by the per-IP rate limit.
- **Rotating the origin secret:** change the Transform Rule first, then the Fly secret. The other order locks everyone out.
- **Rejected:** Cloudflare Pro, only to exempt one API client from bot rules; and taking rider pages out of the sitemap, which would hide them from families searching by name.
