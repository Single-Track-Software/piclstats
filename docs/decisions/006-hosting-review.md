# 006 — Hosting: stay on Fly, harden the database

**Date:** 2026-09-23  
**Status:** Proposed (Chris to decide)

## Context

Chris asked, ahead of photo evidence for rally timing, whether Fly is the
right place for piclstats to grow or whether it should move to something
like Vercel + Supabase.

What runs today:

- One Fly machine (`shared-cpu-1x`, 512 MB, region `ord`, one kept warm)
  running uvicorn; `alembic upgrade head` as the release command; GitHub
  Actions deploys on merge to `main`.
- An **unmanaged** Fly Postgres app (`piclstats-db`, one machine, 1 GB after
  the 2026-09-15 incident, when a slow query took the site down and a
  machine restart recovered it). Fly's own docs title this product "This Is
  Not Managed Postgres": no automatic failover, backups are ours to run.
- The nightly race discovery runs from GitHub Actions over `fly ssh console`;
  admin recipes (checks, scorecard, seed) use the same console.
- No object storage. Static assets ship inside the Python wheel.

What the load looks like: a league of ~1,000 riders and their families and
coaches; page views in the hundreds per day; a rally day adds up to eight
phones syncing a handful of records every 20 s (a 300-rider, 2,100-crossing
rally reconciles in 0.4 s). Scale is not the problem. Resilience, backups
and a place for photos are.

## Options

**A. Stay on Fly and harden it.** Keep the app as it is. Move the database to
Fly Managed Postgres (Basic plan, $38/month at 2026 pricing, unchanged in
the October 2026 update): every plan runs a primary and a replica with
automatic failover, backups and connection pooling, on the same private
network as the app. Add a Tigris bucket (Fly's S3-compatible object store)
for rally photos when phase 2b starts. Everything else, including the
`fly ssh console` operations and the release-time migration, stays.

**B. Vercel + Supabase.** FastAPI would run as Vercel Python functions under
Fluid compute (several concurrent requests per instance, up to 800 s per
request on paid plans, 4.5 MB request payloads). There is no persistent
process and no console: the nightly discovery and every `fly ssh console`
recipe becomes an HTTP endpoint behind a secret or a GitHub Action with
database access, migrations move into the build, and the scraper's
politeness delays run inside a function's time budget. Supabase Pro
($25/month) gives a managed Postgres with backups, dashboards and storage
buckets; the free tier pauses after a week idle, so it is not an option.
Vercel's Hobby plan is non-commercial, so Pro ($20/month) applies. Roughly
$45/month, a migration project of a week or two, and more moving parts, in
exchange for managed database and storage we can get on Fly.

**C. Fly app + Supabase database.** Keeps the app as a process but puts
every query across providers. Pages here run many small queries, so 20 to
40 ms per round trip becomes a visible slowdown, plus egress. Not
recommended.

## Decision (proposed)

Option A. Nothing about piclstats needs a serverless platform, and the
things that would move (a persistent process, a console, migrations at
release) are what the operations recipes are built on. The database is the
weak point, and it is fixed by one plan change, not a platform change.

Steps, in order, each its own PR or runbook entry:

1. Create a Fly Managed Postgres Basic cluster, migrate the data (dump and
   restore during a quiet window, then point `DATABASE_URL` at it), verify,
   and retire `piclstats-db`. Until then, take a nightly `pg_dump` from the
   GitHub Actions job to a private bucket so there is a restore point.
2. Raise `min_machines_running` to 2 only if a deploy ever causes a visible
   gap; today one warm machine and auto-start is enough.
3. Photos (phase 2b): Tigris bucket, presigned uploads from the station page,
   keys stored on the crossing, retention job per the release-form policy.

## Consequences

- Running cost rises from a few dollars to about $40 a month, all of it the
  managed database.
- No code changes for the move; `PICLSTATS_DATABASE_URL` is the only knob.
- Revisit if PICL Stats ever serves several leagues (see
  `docs/backlog.md`): that is when a control-plane rethink, not a hosting
  move, would be due.

## Sources

- [Fly Managed Postgres](https://fly.io/docs/mpg/) and
  [plans](https://fly.io/mpg/); [pricing update effective 2026-10-01](https://fly.io/pricing-update/)
- [This Is Not Managed Postgres](https://fly.io/docs/postgres/getting-started/what-you-should-know/)
- [Vercel Functions limits](https://vercel.com/docs/functions/limitations) and
  [Fluid compute](https://vercel.com/docs/fluid-compute)
- [Supabase pricing](https://www.jetadmin.io/blog/supabase-pricing-2026-guide-to-plans-limits-and-real-world-costs/)
