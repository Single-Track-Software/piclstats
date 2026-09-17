# 003 — Race-place forecasts use relative lap-time ratings, not pace

**Date:** 2026-09-17  
**Status:** Accepted

## Context

The forecast page predicts where a rider would place in another division by
comparing min/mile against a pooled pace distribution. Min/mile divides by
loop distance, and every course still carries the league defaults (2.0 mi MS /
3.5 mi HS). Within one event the HS/MS median pace ratio runs from 0.49 to
1.26 between venues, so pace is not comparable across loops, and only roughly
across courses.

Coaches asked for two tables: where a rider *would have* placed in each
division in their last five races, and where they are *forecast* to place in
each division at each upcoming race.

## Decision

- **Past races** compare the rider's pace with each division's field on the
  same loop the same day, where distance cancels. The only adjustment is the
  existing fatigue % per extra lap. MS and HS divisions are not compared.
- **Future races** use a new engine (`web/ratings.py`) that never uses
  distance:
  - *Score* = log average lap time minus an event effect, fitted per loop and
    gender by alternating means over rider-season abilities, so a conference
    race with a strong or weak field stays comparable with a state race.
  - *Rating* = mean of the last 5 scores, weight 0.6 per race back and a
    further 0.25 on anything from an earlier season; its spread combines
    race-day noise (6.5%, 10% when only last season is known) with the
    uncertainty of the mean. Unrated riders take their division's median.
  - *Forecast* = the rating ranked against the division's current-season
    roster (one conference's for a conference race), integrated over a grid
    of the rider's own good and bad days — deterministic, no random draws —
    giving a median place and an 80% range.
  - Places are a share of the field scaled to an *expected field size*: the
    latest like-for-like race this season, else last season's average. The
    known roster after a season opener undercounts starters by about a fifth.
- Upcoming races come from a hand-entered calendar (`scheduled_races`,
  migration 014, `/admin/schedule`): date, course (lap counts come from its
  profile) and field (state or one conference). A race is upcoming by date
  alone and is never linked to the event its results load as, so the nightly
  pipeline is untouched. With no calendar, the page shows one generic state
  race.
- Parameters are chosen and defended by replay: `piclstats forecast-backtest`
  forecasts every division field from earlier results only.

## Evidence (2024–26, 4,130 forecasts, 157 fields)

| previous → this race | model | same share as last race |
|---|---|---|
| conference → conference | 10.9 | 11.7 |
| conference → state | 11.3 | 12.6 |
| state → conference | 10.6 | 11.2 |
| state → state | 12.2 | 11.9 |
| all | 11.5 | 11.9 |

Mean error in points of field share. The 80% range holds the actual place 77%
of the time in places and 92% as a share of the field; the gap is field size,
which swings from 490 to 760 starters between state races.

## Consequences

- The error floor is race-day noise (a rider's lap time varies about 6% race
  to race against a 14% spread between riders). The model earns its keep when
  the draw changes and across divisions, not by beating "same as last time"
  in a rider's own division. The page says so and always shows the range.
- Before a season's first race there is no roster, so no future table.
- The single-division pace forecast stays as it was, now the third section.
  Moving it onto ratings, measuring fatigue from lap splits instead of the
  3% setting, and an MS→HS bridge fitted from riders who made the move are
  follow-ups.
- Rating parameters live in `RATING_CONFIG`, not the admin forecast form.
