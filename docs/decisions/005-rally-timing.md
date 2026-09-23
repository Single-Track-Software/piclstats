# 005 — Rally timing on volunteers' phones, inside piclstats

**Date:** 2026-09-23  
**Status:** Accepted (phase 1 in progress; first live use planned for the 2027 season)

## Context

PICL rallies are timed across several segments, each with its own start and
finish point, by volunteers with no training beyond a race-day briefing.
Pen-and-paper timing at a 2026 rally produced results that were scrapped.
The requirements (`docs/requirements/PICL Rally Timing — Requirements.pdf`)
ask for complete, auditable, same-day results at near-zero cost, from up to
eight timing points with little or no signal, flowing into piclstats.com
without re-keying. Scoring is the sum of segment times; one run per segment;
no cutoff; a rider who misses a later segment is a DNF; PICL wants CSV.

## Decision

- **A station is a page on piclstats.com**, not an app-store install. A
  volunteer joins a point by scanning a printed QR code that carries an
  unguessable station code; there is no account. The page installs a
  service worker and stores every record in the phone's own database, so it
  works with no signal all day and survives a locked or restarted phone.
  Plain JavaScript, no framework.
- **A crossing is one tap.** Each tap writes a record with a client-generated
  id and the device time; the plate is attached afterwards and validated
  against the roster. Several riders in a second are several taps.
- **Records are append-only.** A correction is a new record that supersedes
  the one it replaces, with who, when and why; a void is a superseding record
  marked void. Sync posts batches and the server upserts by id, so a record
  sent twice never duplicates.
- **Clock trust.** Every phone records its offset from server time at join
  and at every sync; each crossing carries the offset in force, and the
  server stores a corrected time. The lead's view shows each station's
  offset and flags any device whose offset moved more than a second between
  syncs. A countdown tap at the briefing measures offsets before anyone walks
  out. No signal is needed during the event.
- **Timing-lead pages live under `/admin/timing`**, gated to the `picl` role
  and not linked from the public site. Setup (event, segments, station codes,
  roster), station status, the flag list, corrections and approval all live
  there.
- **Approval publishes a normal rally event** (`events.event_type = 'rally'`)
  with one results row per rider: total time = sum of segment times,
  segment splits kept alongside, place by category. Results, rider pages and
  the CSV export need no special casing. Points stay out until PICL's rally
  scoring is known.
- **No-internet transfer** is the station's export file plus AirDrop or
  Share; a last-resort import of that file on the lead's device.
- **Segments carry distance, elevation gain and loss, and which groups ride them**
  (HS, MS, or both), on the timing event rather than the course profile: a
  rally's segments are that day's, and past seasons have no timing event. A
  rider's total is the sum of the segments their group rides, each segment is
  ranked on its own, and the course page for a rally season shows the
  segments read-only. A rider's group comes from the roster category through
  the same division-to-loop mapping the course profiles use.
- **Roster** comes from the season's race results (plates are stable across a
  season: 97% of multi-race riders in 2025 kept one plate), a pasted list, or
  one rider at a time; the registration export can be mapped later.

## Phases

1. Everything marked Must: setup, station page, sync, roster, reconciliation
   and flags, corrections with audit, approval and publish, CSV and file
   export. Delivered as several PRs; this ADR ships with the first (setup).
2. Photos at the moment of recording (object storage, not the Postgres VM),
   provisional results during the event, plate-first entry at start points.
   The hosting platform is reviewed at the same time.
3. Video and plate suggestion from photos: not planned.

## Consequences

- New tables: `timing_events`, `timing_segments`, `timing_points`,
  `timing_roster`, `timing_devices`, `timing_crossings`. Nothing touches the
  results tables until a rally is approved.
- Station codes are the access control for recording; the code sheet is kept
  by the lead and cut up per captain.
- A rally has no raceresult id, so `events.raceresult_id` is nullable
  (migration 018; the unique constraint already treats NULLs as distinct).
  The results page shows "PICL rally timing" in place of the raceresult
  link, the data-quality checks skip events without one (the lead's
  reconciliation is their gate), and `classify_event_types` marks any event
  a timing rally published as `rally` regardless of name or course flag.
- Published results put segment times in the lap columns (`lap1..lap6`, up
  to six segments, in the order the rider's group rides them), the penalty
  in `penalty`, and the segment ranks and adjustments in `raw_data`. The
  results page labels them S1.. for rally events. Reopening hides the public
  event until it is published again; publishing again rewrites the same
  event and its results rows.
