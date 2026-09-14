# 001 — Course loop and lap profiles are per season

**Date:** 2026-09-14  
**Status:** Accepted

## Context

Pace (min/mile), staging speed ratings, and finish-time forecasts all divide a
rider's time by laps × loop distance. Until now every course used one loop
distance (2.0 mi MS / 3.5 mi HS defaults) and one lap count per division taken
from the league spreadsheet, for every season.

Comparing those profiles with the laps riders actually recorded showed they
match at only a couple of venues: Varsity rides 5 laps at Granite and 6 at
Boyce, Oesterling, Coleman, and Wainer against a 4-lap profile, women's HS
divisions ride one lap fewer than men's, and weather changes counts on the
day (Penn College 2026 Varsity dropped from 4 to 3 after overnight rain).
Courses are also re-routed between years.

## Decision

- `course_loops` and `division_laps` gain a season dimension. A row with
  `season = NULL` is the default; a row for the event's season wins over it.
  Both unique constraints are `NULLS NOT DISTINCT` so defaults are unique.
- Every query resolves the profile through one shared lateral join
  (`web/queries._lap_joins`) rather than hard-coding the default row.
- `piclstats seed` derives a season row for every course-season with results:
  loop rows copy the course default, lap counts are the most common recorded
  lap count among finishers whose splits add up (minimum 3 finishers, else
  the default). Seeding only ever inserts, so admin edits are never overwritten.
- `/admin/courses/{id}` edits the defaults and each season (MS/HS distance and
  gain, lap count per division and gender) and shows the recorded lap count
  beside each entry, highlighting mismatches.
- Profiles are keyed by course + season, not by event. Belmont and Blue
  Mountain host two events per season; a per-event override can be layered on
  later if a day-specific change matters.

## Consequences

- Historical pace already used recorded laps; loop distance and the forecast
  "expected laps" now vary by season, so numbers shift wherever an admin
  enters a real distance for a course-season.
- The forecast's division-to-division profile lookup still reads the defaults;
  making it course- and season-aware needs a course picker on the forecast page.
- After deploying, run `piclstats seed` against production once to create the
  season rows (the migration only adds the columns and constraints).
