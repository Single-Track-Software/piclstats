# Decisions

Append-only log. Full records live in `docs/decisions/`; supersede, never edit.

- 2026-09-14 — [001](docs/decisions/001-per-season-course-profiles.md) Course loop and lap profiles are per season (NULL = default), seeded from recorded laps, editable per season in admin.
- 2026-09-16 — [002](docs/decisions/002-data-quality-layer.md) Data-quality layer modelled on gvpd: per-scrape runs, row-level checks, lineage, scorecard, publish gate, admin DQ tab (proposed).
- 2026-09-17 — [003](docs/decisions/003-relative-ratings-forecast.md) Race-place forecasts rank relative lap-time ratings against the expected field (no loop distance); calendar entered in admin; parameters set by `piclstats forecast-backtest`.
- 2026-09-21 — [004](docs/decisions/004-course-season-race-type.md) Race type (race | rally) is a per-course, per-season flag with a default; `events.event_type` is resolved from it, exhibitions stay a name pattern.
- 2026-09-23 — [005](docs/decisions/005-rally-timing.md) Rally timing runs on volunteers' phones as offline station pages inside piclstats; append-only crossings, clock offsets per device, lead approves and publishes as a rally event.
- 2026-09-23 — [006](docs/decisions/006-hosting-review.md) Hosting review (proposed): stay on Fly; move the database to Fly Managed Postgres; Tigris for photos later. Vercel + Supabase rejected for this app's shape.
- 2026-09-24 — [006](docs/decisions/006-hosting-review.md) Accepted: production database moved to Fly Managed Postgres (`piclstats-pg`); old `piclstats-db` stopped, to be destroyed after a week.
- 2026-10-08 — [007](docs/decisions/007-cloudflare-edge-and-keyed-api.md) Cloudflare (free) proxies piclstats.com with bot protection; the origin serves only Cloudflare (secret header), except /healthz and a keyed, team-scoped /api/v1 on piclstats.fly.dev for automated partners.
- 2026-10-13 — [008](docs/decisions/008-conference-series-scoring.md) Conference standings use the handbook's series score: no State Championship, DNF points count, lowest race dropped when the series held four or more; matches raceresult's season lists (1,146 of 1,149, 2024–26).
- 2026-10-14 — [009](docs/decisions/009-ttc-bonus-and-champion-tie-break.md) Standings add TTC bonus points read from raceresult's season lists (`piclstats ttc-sync`, nightly), and a tie for 1st is broken by the handbook rule (conference wins from points); other ties stay shared.
- 2026-10-14 — [010](docs/decisions/010-rallies-in-the-conference-series.md) Rallies load from raceresult segment lists (points and places, no laps); `events.counts_in_series` puts a rally in the conference series when the league scored it (2026 Eastern Gold yes, Central no). Supersedes 004 on rally points.
