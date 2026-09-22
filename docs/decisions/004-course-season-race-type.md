# 004 — Race type is a course-season flag

**Date:** 2026-09-21  
**Status:** Accepted

## Context

Whether an event counts toward standings (`events.event_type`) was derived
from the event name: `%rally%` made it a rally, `%exhibition%` / `%short
track%` an exhibition, anything else a points race. The 2026 Central #1 at
Birdsboro is a rally with no "rally" in its name, and the results page
carries no type at all, so there was nowhere to record the fact before its
results load. A rally also does not post points, so it never belongs in a
leaderboard, and a scheduled rally has no place to forecast.

## Decision

- `course_race_types (course_id, season, race_type)` holds `race` or `rally`
  per course and season, with a `season = NULL` default row, the same shape
  as the loop and lap profiles (ADR 001). It is edited in each season block
  of `/admin/courses/{id}`.
- `events.event_type` is resolved from it: exhibitions stay a name pattern
  (Johnstown 2024 hosted a points race and a short-track exhibition in one
  season), then the season row, then the course default, then the `%rally%`
  name pattern for events with no course or flag. Saving the admin form
  re-runs the classification, as does every seed.
- `piclstats seed` inserts a row for every course-season with events, taken
  from the event names the first time; rows are never overwritten by a
  re-seed. The migration backfills the same rows so nothing changes on deploy.
- Scheduled races at a rally course-season are left out of the forecast's
  future-races table.
- Rally results still load through the pipeline (when the client can read
  them) and show on the rider page badged non-scoring. How rally points get
  in is open until the league's rally points format is seen.

## Consequences

- Lineage records an event classification as `manual` when the flag decided
  it and `pattern` when only the name did.
- A course that hosts both a race and a rally in the same season needs a
  per-event override; none has yet.
