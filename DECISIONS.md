# Decisions

Append-only log. Full records live in `docs/decisions/`; supersede, never edit.

- 2026-09-14 — [001](docs/decisions/001-per-season-course-profiles.md) Course loop and lap profiles are per season (NULL = default), seeded from recorded laps, editable per season in admin.
- 2026-09-16 — [002](docs/decisions/002-data-quality-layer.md) Data-quality layer modelled on gvpd: per-scrape runs, row-level checks, lineage, scorecard, publish gate, admin DQ tab (proposed).
- 2026-09-17 — [003](docs/decisions/003-relative-ratings-forecast.md) Race-place forecasts rank relative lap-time ratings against the expected field (no loop distance); calendar entered in admin; parameters set by `piclstats forecast-backtest`.
- 2026-09-21 — [004](docs/decisions/004-course-season-race-type.md) Race type (race | rally) is a per-course, per-season flag with a default; `events.event_type` is resolved from it, exhibitions stay a name pattern.
- 2026-09-23 — [005](docs/decisions/005-rally-timing.md) Rally timing runs on volunteers' phones as offline station pages inside piclstats; append-only crossings, clock offsets per device, lead approves and publishes as a rally event.
- 2026-09-23 — [006](docs/decisions/006-hosting-review.md) Hosting review (proposed): stay on Fly; move the database to Fly Managed Postgres; Tigris for photos later. Vercel + Supabase rejected for this app's shape.
