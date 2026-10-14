# 010 — Rallies load with points, and count in a series when the league scored them

**Date:** 2026-10-14  
**Status:** Accepted (Chris). Supersedes the part of [004](004-course-season-race-type.md) that said a rally doesn't post points and never belongs in standings.

## Context

ADR 004 kept rallies out of standings because they weren't thought to post points. In 2026 two conference races were rallies, both the "Rambling Rally at Birdsboro Preserve":
- **Eastern Gold #2** (raceresult 424683)
- **Central #1** (raceresult 401233)

pamtb.org never linked either race, so piclstats never loaded them. Both post places and series points on raceresult.

The league's season lists treat them differently:
- **Eastern Gold #2 counts.** Its column carries each rider's rally points, and Eastern Gold's series is four races, best three.
- **Central #1 scores 0 for every rider.** Since 0 is always the dropped score, Central's totals equal the other three races.

A rally's raceresult lists differ from a race's:
- They have timed segments instead of laps.
- Their individual lists are filtered one team at a time, or come back empty through the API.
- The segment lists hold every rider, grouped by category, with bib, name, place and points, but no team. DNFs appear to be left out.

## Decision

1. **The parser reads a rally from its segment lists** (`parse_rally_list`), taking place, status and points. It never stores segment times as laps or a total time, so rallies stay out of pace, form, ratings and forecasts, which use points races only.
2. **Each rally rider gets a team from their other races that season** (`quality.ingest.fill_rally_teams`), so they link to the same rider record and conference. Matching is done within the rally's own conference, by category and name, then by category and bib.
3. **A new `events.counts_in_series` flag** (migration 030) decides whether an event is in the conference series. NULL means the default: a points race counts and a rally doesn't. Standings, the per-race columns, the drop rule and team standings use `COALESCE(counts_in_series, event_type = 'points')`.
4. **`piclstats load-rally <id> --season --order [--counts-in-series | --not-in-series]`** loads a rally in date order, moving later races down one place, and sets the flag. Check the conference's season list on raceresult to decide which flag to use.
5. **Labels:**
   - The rider's race history tags a counting rally "rally" and a non-counting one "non-scoring".
   - Other views tag any rally "rally" and keep "non-scoring" for exhibitions.

## Result (NAS copy)

- **Eastern Gold 2026:** totals now match raceresult for all but 5 riders, down from 182. Three of the five are rally DNFs (100 points each) missing from the segment lists. The other two changed category.
- **Central 2026:** unchanged, and still matches.

## Not yet

- **Rally DNFs.** They're only in the team-filtered list, which we can't read.
- **The 2022 and 2023 rallies** stay out of standings by default. Those seasons have no conference standings anyway.
- **A rally that pamtb.org later links** is loaded by nightly discovery, but it doesn't count until it's flagged.
