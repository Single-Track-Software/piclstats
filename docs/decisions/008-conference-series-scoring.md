# 008 — Conference standings follow the handbook's series scoring

**Date:** 2026-10-13  
**Status:** Accepted (Chris).

## Context

Conference standings summed every points race a rider rode, so the State Championship added to conference totals. For example, Emerson Poggioli showed 1,632 points (2026, Eastern Blue, 8th Grade Girls). The league's own series list on raceresult shows 1,241.

The 2026 PA NICA Handbook (5.14–5.17, pp. 28–29) sets the rules:
- **What counts:** every race is in the conference series except the State Championship, which stands alone. The State Kick-Off counts.
- **DNFs:** a rider who starts but doesn't finish a lap still earns 100 points.
- **Drop rule:** the lowest conference series score is dropped, and a missed race counts as that dropped score. Series of fewer than four races drop nothing.
- **TTC:** Teen Trail Corps bonus points are added to the total.
- **Ties:** broken by most series wins, then most 2nds, and so on, then the last race's finish.

raceresult's "Season Overall" lists, the timer's series standings, show how this was applied:
- 2024 Eastern and 2025 Eastern Blue held four races, and each rider's best 3 of 4 counts.
- 2026 Eastern Blue held three races (Conference #2 was cancelled), and nothing is dropped.
- The drop applies to middle school as well as high school, although the handbook only names high school categories.

## Decision

Standings (`queries.leaderboard` ranked by total, and everything built on it: conference standings, rider season cards, the director digest, the API) use the series score:

1. The State Championship (`queries.state_championships`) is excluded. Team standings exclude it too.
2. DNF points count, including DNFs whose timing failed the quality gate. Averages and best place are taken over finishes only.
3. `standings.series_total`:
   - If the rider's conference held four or more series races that season, drop the lowest score. If the rider missed a race, the missed race is the drop.
   - Otherwise, sum all scores.
4. Standings list everyone who scored, as raceresult's lists do. The two-race minimum now applies only to average-based rankings.

**Checked against raceresult** for 2024 Eastern, 2025 Eastern Blue and 2026 Eastern Blue: 1,146 of 1,149 riders' totals match. The 3 that differ look wrong in raceresult's own list:
- a Kick-Off score of 0 where its own Kick-Off results show 215 points
- two riders whose series scores aren't from their own races

## Not yet

- **TTC bonus points.** raceresult publishes them in the same season list, but piclstats doesn't ingest them yet. Totals here are the series score without TTC, so rank can differ where TTC breaks a gap.
- **The handbook tie-break.** Ties still go to more races, then name.
- **Team series scoring.** Teams are scored with a scoring matrix by division (5.16–5.17), which piclstats doesn't model. Team totals remain sums of rider points.
- **Showing dropped races** on the standings page, as raceresult does. That's next.
