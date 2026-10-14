# 009 — TTC bonus points and the champion tie-break in standings

**Date:** 2026-10-14  
**Status:** Accepted (Chris). Follows [008](008-conference-series-scoring.md), which left both out.

## Context

The handbook (5.15) adds Teen Trail Corps (TTC) bonus points to the conference series score for the final ranking. A tie for conference series champion is then broken by:
1. most conference race wins
2. then most 2nds, 3rds and so on
3. then the finish in the last series race

Below 1st, the league's own series lists on raceresult leave ties shared.

TTC hours live in a league spreadsheet fed from PitZone, which piclstats can't reach. The league loads the bonus into raceresult, though. Each conference's events publish a season list (`03 - Season Overall Results|…`) with a `TTCBonusPoints` column.

The tie-break can't be applied without TTC:
- **2024 Central 6th Grade Boys:** Aaron Scholnick and Freddie Gray were level on 1,340 series points. TTC (+21) made Scholnick champion. The tie-break alone would have named Gray.

## Decision

1. **`piclstats ttc-sync [--season …]`** reads each published race's season list, newest race first, and keeps each rider's newest bonus in a new `ttc_bonus` table (migration 029).
   - Riders are matched on category, bib and name, because 2026 reuses bib numbers across conferences. Category and bib alone are used only where one rider wore that bib in that category.
   - Where an event has both a full list and a "Top5" list, the fuller one is used.
   - A season's rows are only replaced when at least one list was read.
   - The nightly workflow runs it after discovery.
2. **Standings total = series score + TTC.** The standings page shows a TTC column, with "—" until the league publishes TTC for that conference.
3. **A tie for 1st is broken by the handbook rule; other ties stay shared.** Finishes are read from points, not places: at state races, points follow the rider's place within their own conference.

## Checked against raceresult's TTC season lists

- **2024:** 998 of 1,010 riders' totals match.
- **2025:** 1,006 of 1,016 match.
- **2026 Eastern Blue:** everyone matches except Jesse Davis, whose 0 in raceresult's list is the same one noted in ADR 008.

The remaining differences:
- **Category moves:** riders who changed category mid-season, which raceresult splits differently.
- **Odd raceresult rows**, such as a girl listed in a boys' category.
- **2024 Western JV3 Boys:** eight riders are 5–6 points apart, not yet explained.
- **2026 Eastern Gold and Central are off** because each conference's "Rambling Rally at Birdsboro Preserve" (raceresult 424683 and 401233) was a series race that piclstats never loaded. The scraper can't read raceresult's rally layout, and pamtb.org didn't link those races.
- **2024 Western Varsity Girls** are level on 1,715 including TTC. By the handbook rule Giuliana Contini has two conference wins to Larkin McCloskey's one, so we rank Contini 1st. Raceresult lists McCloskey 1st, so raceresult's order for that tie doesn't follow the handbook.

## Not yet

- **2026 rally series races.** These need rally points loaded to fix Eastern Gold and Central.
- **Western 2026 TTC.** Western's last race has no TTC list yet, so its riders show "—".
