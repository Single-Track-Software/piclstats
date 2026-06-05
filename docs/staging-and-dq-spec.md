# PICL Stats — Staging / Speed Rating + Data-Quality Lineage

**Status:** Draft spec for review
**Author:** Chris + Claude
**Date:** 2026-06-05
**Context:** Post-demo. PICL leadership (Ariana) has refined a z-score staging metric over
years; cross-division *prediction* (already shipped) was the standout they'd never tackled.
Goal: bring their staging metric into PICL Stats, on the same screen as the forecast, and
(backlog) add a GVPD-style data-quality + lineage layer so the platform is solid enough to
float to NICA.

---

## Part A — Staging / Speed Rating (build now)

### A.1 The metric

PICL's hand-rolled staging metric is a **z-score** (standardized score):

```
z = (rider avg lap time − category avg lap time) / stdev(category)
```

- **Category = the whole age group + gender** (ALL Middle-School Males, ALL High-School Males…),
  not the specific race division.
- Lower lap time = faster, so a fast rider has a **negative** z. Sorting ascending by z gives the
  staging order (fastest at the front).

Ariana's workflow (per her spreadsheet): one sheet per season, a **z per event**, then a
**"best-of" or "average"** roll-up that is sorted to produce the staging order.

We will compute **both flavors, shown together** (Chris's call: "do both, same screen"):

1. **PICL-exact (raw lap time, per event)** — reproduces Ariana's spreadsheet number for number.
   Within a single event + age group everyone rides the same loop, so raw lap time is directly
   comparable. This is the *trust* metric: it must match her sheet.

2. **Course-normalized (min/mile, season-to-date)** — the upgrade. PICL Stats already computes
   course-normalized min/mile per result (consistency-gated, pace-sanity-ranged). A z built on
   that is comparable **across courses and the whole season**, so we can stage the *next* race off
   a rider's season-to-date rating, not just their last event — something the spreadsheet can't do.

### A.2 Why per-event z, then aggregate (not one pooled z)

Each event has its own conditions (weather, course, who showed up). A z-score computed *within an
event* cancels those shared shifts, so a rider's z measures their standing **relative to the field
that day**. Aggregating per-event z's across the season then compares like with like. This is the
statistical justification for Ariana's approach, and we keep it.

**Aggregates (both shown, sortable):**
- **Average z** — mean of the rider's per-event z's. Stable; reflects typical speed.
- **Best-of z** — the rider's single most-negative per-event z. Reflects ceiling / potential.

Staging order = sort ascending by the chosen aggregate.

### A.3 Age-group definition

Age group falls out of existing data — no new inputs:

- `age_group ∈ {MS, HS}` from `division_laps.loop_type` (MS-loop divisions = middle school,
  HS-loop divisions = high school).
- `gender` from `results.gender`.
- Population for an event = all riders in that `(event, age_group, gender)` across every division
  in the age group (e.g. all MS-loop divisions: 5th–8th grade + MS Advanced).

### A.4 Backend design

**New module:** `src/piclstats/web/staging.py` (pure functions, mirrors `forecast.py` style).

**Core query** — compute per-event, per-rider z in SQL with window functions (one pass), for
*both* metrics. Partition by `(event_id, age_group, gender)`:

```sql
-- per rider/event: their pace + the age-group population's mean/stdev that event
WITH base AS (
    SELECT
        e.id   AS event_id,
        e.season,
        e.event_order,
        COALESCE(ra.canonical_id, ri.id) AS rider_id,
        dl.loop_type                     AS age_group,   -- 'MS' | 'HS'
        r.gender,
        -- raw avg lap time (seconds), consistency-gated
        CASE WHEN <LAPS_CONSISTENT> THEN
             EXTRACT(EPOCH FROM r.total_time) / NULLIF(<ACTUAL_LAPS>, 0) END AS lap_secs,
        -- course-normalized min/mile (existing formula, gated + pace-ranged)
        <MIN_PER_MILE_EXPR>                                                  AS min_per_mile
    FROM results r
    JOIN events e        ON r.event_id = e.id AND e.event_type = 'points'
    JOIN riders ri       ON r.rider_id = ri.id
    LEFT JOIN rider_aliases ra ON ra.rider_id = ri.id
    JOIN division_laps dl ON dl.course_id = e.course_id AND dl.division = r.division
                          AND (dl.gender = r.gender OR (dl.gender IS NULL AND r.gender IS NULL))
                          AND dl.season IS NULL AND dl.loop_type IS NOT NULL
    LEFT JOIN course_loops cl ON cl.course_id = e.course_id AND cl.loop_type = dl.loop_type
    WHERE r.status = 'OK' AND r.place IS NOT NULL
),
z AS (
    SELECT *,
        (lap_secs     - avg(lap_secs)     OVER w) / NULLIF(stddev_samp(lap_secs)     OVER w, 0) AS z_lap,
        (min_per_mile - avg(min_per_mile) OVER w) / NULLIF(stddev_samp(min_per_mile) OVER w, 0) AS z_pace,
        count(*) OVER w AS field_size
    FROM base
    WINDOW w AS (PARTITION BY event_id, age_group, gender)
)
SELECT * FROM z;
```

Then aggregate per rider in a thin layer (SQL `GROUP BY rider_id` or Python):
`avg(z)`, `min(z)` (best-of), `count(events)`, plus latest-event z for "current form".

**Reuse** existing fragments: `_ACTUAL_LAPS`, `_LAPS_CONSISTENT`, `_PACE_MIN/_PACE_MAX`,
`total_time < interval '2 hours'`, canonical-id resolution.

**New query functions** (`queries.py`):
- `rider_speed_rating(session, rider_id)` → season-to-date avg/best z (both metrics) + per-event series.
- `staging_grid(session, season, age_group, gender, sort='avg', metric='pace')` → ranked riders.

### A.5 UI

**(1) Fold a Speed Rating panel into the existing forecast screen** (`forecast.html`) — keeps the
prediction and adds the new data, per Chris's "same screen" call:
- Left column, new card under **Current Pace**: **Speed Rating** — season-to-date avg z and best z,
  labeled in plain English ("**+1.3 SD faster** than the HS Male field — top 9%").
- New section: **Standing in age group** — a bell curve of the `(age_group, gender)` distribution with
  the rider marked, plus percentile.
- New section: **Per-event z-scores** — small table/line chart of z per event across the season,
  showing both the PICL-exact (lap-time) and normalized (min/mile) columns side by side. This is the
  Ariana-spreadsheet view, automated.

**(2) New Staging page** (`/staging`) — the directly-actionable deliverable for race organizers:
- Pick **season + age group + gender** (later: a specific event).
- Ranked grid of all riders by chosen aggregate (avg / best-of) and metric (lap-time / min-mile),
  with z, percentile, #events, and current division.
- **CSV export** for call-up sheets.
- Toggle sort (avg ↔ best-of) and metric so they can compare against their spreadsheet live.

### A.6 Sign / display convention

Store raw z (negative = faster, matches Ariana). For display, flip to an intuitive
"**+ = faster than field**" speed rating, clearly labeled. *Confirm with Ariana which her staff
read instinctively before locking the UI.*

### A.7 Edge cases / gates

- **Small populations:** suppress or flag z when `field_size < 8` or `stddev = 0` (unstable).
- **Few events:** a rider with 1 event gets a rating but flagged low-confidence; need ≥2–3 for a
  stable average.
- **No gender / legacy divisions** (e.g. exhibition-only "Advanced"): excluded — already filtered by
  `event_type='points'` and the `division_laps` join (no loop_type → no age group).
- **Bad lap data:** per-event consistency gate drops that event for that rider; their other events
  still count.
- **DNF/DNS:** excluded (`status='OK'`, valid `total_time`).

### A.8 Validation (the trust step)

Before shipping: get **one of Ariana's actual season spreadsheets** and reproduce her per-event z and
her best-of/average ordering **exactly** with the PICL-exact (lap-time) flavor. Diff row by row. Only
then layer the normalized season-to-date version on top. This is what turns "impressive demo" into
"they rely on it."

### A.9 Phasing

- **Phase 1** — z-score query + `staging.py`; Speed Rating panel + per-event table on the forecast
  screen. Validate against Ariana's sheet.
- **Phase 2** — `/staging` grid page with sort/metric toggles + CSV export.
- **Phase 3** — once Strava course distances land, the min/mile flavor sharpens automatically
  (better cross-course normalization → better season-to-date ratings).

---

## Part B — Data-Quality + Lineage layer (backlog, modeled on GVPD)

**Why:** To float this to NICA, the data has to be *demonstrably* clean. GVPD's pattern — an
append-only **lineage** log + a **scorecard** of quality metrics + a **golden-set** no-regression gate
— makes every canonical entity explainable and every quality number trackable. PICL Stats already does
a lot of canonicalization; it just doesn't *record or show* it.

### B.1 What PICL already canonicalizes (today, silently)

| Mechanism | Where | Source/method recorded? |
|---|---|---|
| Rider merge across teams/seasons | `rider_aliases` | partial (`match_method`) |
| Conference lineage (Blue/Gold → Eastern) | `team_conferences` | `source`, `conference_group` |
| Division label fold (Middle School Advanced → MS Advanced) | `seed.normalize_divisions` | no |
| Event classification (rally/exhibition non-scoring) | `seed.classify_event_types` | no |
| Event → course mapping (name patterns) | `seed.map_events_to_courses` | no |

### B.2 Borrow GVPD's three pieces

1. **Lineage table** (`picl_lineage`, append-only) — one typed edge per raw→canonical link:
   `run_id, stage (rider_merge|conference_norm|division_norm|event_classify|course_map),
   canonical_key, raw_value, source, mechanism (auto_name|manual|alias|pattern|vote),
   match_score, volume (results affected), created_at`. Each pipeline step emits edges as a side
   effect, so lineage is complete by construction (GVPD's key idea). Plus a `picl_lineage_runs`
   summary row per build.

2. **Scorecard** (`picl_dq_metrics`) — metrics persisted per run, tracked for regression:
   `% results with valid pace`, `% events mapped to a course`, `% riders merged / remaining
   duplicate names`, `% events classified`, `% results with consistent laps`, distinct-division
   sanity, etc. (mirrors GVPD `scorecard.py`).

3. **Golden set** — a small hand-verified set ("LUKE RYAN is two different people", "Colton Martin
   id=4766 = Lower Bucks Pennsbury", "Fair Hill 2022 = rally/non-scoring") that the scorecard checks
   each run — the no-regression gate.

### B.3 UI — admin "Data Quality" page

- **Scorecard** with current values + trend since last run (green/amber/red vs thresholds).
- **Lineage Inspector** (GVPD's "Data Lineage Inspector"): pick a rider / division / course / event
  and see exactly what fed it — which raw variants, from which source, via which mechanism, how much
  volume. This is the credibility artifact for the NICA conversation.

### B.4 Phasing

Backlog — after staging Phases 1–2. Lineage table + emit-on-canonicalize first (cheap, since the
canonicalization code already exists — just record edges), then scorecard, then the admin page.

---

## Open decisions (need Chris / Ariana input)

1. **Ariana's exact aggregate + sign convention** — best-of vs average as the *default* sort, and
   does her staff read "faster = negative" or do they flip it? (Drives the default UI.)
2. **A sample season spreadsheet** from Ariana to validate Phase 1 against, number for number.
3. **Confirm age group = `loop_type (MS/HS) × gender`** is the population she standardizes against
   (vs. e.g. splitting MS Advanced out, or grade bands).
4. **Staging page scope for v1** — season+age-group rollup (matches her sheet) vs. per-specific-event.
