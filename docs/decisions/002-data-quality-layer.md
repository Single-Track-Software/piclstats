# 002 — Data-quality layer modelled on gvpd: checks, lineage, scorecard, gate

**Date:** 2026-09-16  
**Status:** Proposed

## Context

Race scrapes are manual and will stay so until we trust what lands. Today the
only quality controls are guards buried inside read queries: `total_time <
2 hours`, "splits add up within 10 s", a min/mile sanity band, `status = 'OK'`.
Nothing is recorded per scrape, nothing is surfaced, and the raw data carries
visible garbage that coaches will find on rider pages:

| Problem (prod, 13,808 results) | Rows |
|---|---|
| `total_time` ≥ 20 h (a timestamp column parsed as elapsed time, 2022–24) | 190 |
| `place` ≤ 0 (`-1` accepted by the parser, shown as "Best Place -1") | 17 |
| `status = '*'` leaked through as a status | 21 |
| OK finishers whose splits do not sum to the total | 110 |
| Punctuation-only name variants left unmerged (`O'REILLY`/`OREILLY`, `LA LONDE`/`LALONDE`) | 2 groups |
| Same name, two different kids in one event | 3 groups |
| Team spelling variants (`PGH north`/`Pgh North`/`PGH North`, …) | 4 groups |

gvpd (`~/Development/gvpd`) already solved this shape of problem for vehicle
data: a gated pipeline `canonicalize → dedup → scorecard → gate → publish`,
an append-only lineage table that is the merge decision log, a long-format
metrics table for trends, positive and negative golden fixtures, and an
admin page with a pipeline-flow diagram and a Sankey-style merge map. The
diagrams are server-side inline SVG built in Python (`gvpd/web/routes.py`
`_pipeline_flow_svg`, `_merge_map_svg`), no chart library.

## Decision

Replicate gvpd's structure in piclstats, sized for one source and ~3k riders.

**Tables** (one Alembic revision; integer ids, not uuid):

- `scrape_runs` — one row per event load: event, raceresult id, season,
  started/finished, `status` (`loaded | checked | published | blocked |
  failed`), rows parsed/loaded, riders new, `gate_passed`, `gate_reasons`,
  `detail` (detected column layout).
- `dq_checks` — row-level findings (gvpd has no equivalent; race data needs
  it): run, result, event, `check`, `severity` (`error` = excluded from
  stats, `warn` = kept and flagged, `info`), `observed`, `expected`.
- `picl_lineage` + `picl_lineage_runs` — gvpd's lineage shape: `stage`,
  `level` (`rider | team | division | event | conference`), `canonical_key`,
  `raw_value`, `raw_id`, `source`, `origin`, `mechanism` (`exact | casing |
  punctuation | whitespace | alias | typo | pattern | manual`),
  `match_score`, `volume`. No FK to riders so deletes never erase history.
- `picl_dq_metrics` — long format, one row per metric per run, gate verdict
  stored as `metric = 'gate_passed'` with reasons in `detail`.
- `picl_golden` (positive fixtures: this rider id is that person and team;
  this event is a rally) and `picl_golden_pairs` (negative: these two rider
  ids must never merge). `find_conflicts` seeds the negative set.

**Derived columns**, never overwriting raw values: `riders.name_key` (upper,
NFKD, non-alphanumerics stripped), `riders.team_key` (lower, whitespace
collapsed), `results.dq_status` (`ok | warn | excluded`). Public queries
filter on `dq_status <> 'excluded'` instead of repeating time and lap guards
in five places.

**Pipeline per scrape**: `scrape → load → check → canonicalize → dedup-exact
→ scorecard → gate → publish`. Blocked runs stay loaded but flagged; the
admin page shows why. Every existing silent fold (division aliases, event
type regex, course mapping, conference lineage, rider merges) emits a
lineage edge. Merge blocks on `name_key` with the existing never-co-raced
rule; co-raced same-name groups become negative golden pairs for review.
Cross-season identity stays deterministic; no LLM adjudication in v1.

**Checks**: `total_time_timestamp`, `total_time_over_cutoff` (vs the
division profile), `place_nonpositive`, `status_unknown`, `ok_without_time`,
`splits_dont_sum` (penalty-aware), `laps_ne_profile`, `place_sequence_gap`,
`bib_zero`, `name_empty`, `team_missing`; event-level `results_count_drop`
and `layout_unrecognized`.

**Gate** (`evaluate_gate`, pure, copied from gvpd): golden pass rate ≥ 90 %
and not down more than 2 points vs the previous run; negative pairs 100 %
distinct; timestamp-totals and non-positive places must not increase; this
event's excluded share ≤ 5 %; no results-count drop.

**Admin DQ tab** (`/admin/dq`): pipeline-flow SVG with live counts and the
gate colour, metric cards with delta and sparkline over the last 12 runs,
recent scrape runs with gate reasons, open findings grouped by check, and a
lineage inspector with the merge-map SVG (search a rider, team, division or
course; variants coloured by season). Ported from gvpd's `routes.py`
builders with Tailwind classes; plain GET form, no HTMX.

## Consequences

- Rider pages stop showing 24-hour finishes and negative places; pace,
  staging and forecasts draw from `dq_status = 'ok'` rows only.
- Every merge and fold is explainable from the lineage table, which is what
  the DQ tab's merge map renders.
- A bad scrape (wrong column layout, half the field missing) is held at the
  gate instead of silently replacing good data.
- Adds a `dq` CLI stage to the manual scrape workflow; `seed` and `merge
  auto` become stages it calls.

## Sequencing (one PR each)

1. Migration + tables + derived columns; backfill `name_key`, `team_key`.
2. `quality/checks.py`, `results.dq_status`, wire into `scrape`; replace the
   scattered query guards with the `dq_status` filter (behaviour-preserving
   on today's data, then the parser fix for `place ≤ 0` and `*`).
3. Lineage emission from `seed.*` and `merge.*`; `name_key` blocking in
   `auto_merge`.
4. `quality/scorecard.py`, `evaluate_gate`, `scrape_runs.status`; seed
   golden pairs from `find_conflicts`.
5. `/admin/dq` page with the two SVG ports and the findings table.
