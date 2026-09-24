# PICL Stats

Race-results scraper and analytics dashboard for the PA Interscholastic Cycling League (PICL / PAMTB). Scrapes results from raceresult.com into PostgreSQL and serves a FastAPI web dashboard with leaderboards, rider/team/course pages, race-position charts, staging speed-ratings, and finish-time forecasts.

**Production:** https://piclstats.com (Fly.io app `piclstats`, region `ord`; `www.` and `piclstats.fly.dev` redirect there).

## Stack

- Python 3.11+, FastAPI + Jinja2 templates, uvicorn
- PostgreSQL via SQLAlchemy Core + Alembic migrations (prod DB is Fly Managed Postgres, cluster `piclstats-pg`; ADR 006)
- httpx scraper against the raceresult.com JSON API
- pytest / ruff / mypy for dev

## Setup on a new machine

```bash
git clone https://github.com/Single-Track-Software/piclstats.git
cd piclstats
uv venv --python 3.11        # uv reads .python-version and fetches 3.11 if missing
uv pip install -e ".[dev]"
cp .env.example .env         # then fill in real values — see below
```

(`python3.11 -m venv .venv && pip install -e ".[dev]"` also works if you'd
rather not use uv, but then a 3.11 interpreter has to already be on the box —
a Homebrew upgrade removing `python@3.11` is what broke the venv before.)

`.env` is gitignored and must be created by hand on each machine. All settings are `PICLSTATS_`-prefixed (see `src/piclstats/config.py`):

| Variable | Purpose |
|---|---|
| `PICLSTATS_DATABASE_URL` | `postgresql+psycopg://…` connection string. Unset, it falls back to Fly's `DATABASE_URL`, then `localhost:5432/piclstats`. |
| `PICLSTATS_SESSION_SECRET` | Signs session cookies. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`. **Required in production** — the app refuses to start without it when `SESSION_HTTPS_ONLY` is true. |
| `PICLSTATS_SESSION_HTTPS_ONLY` | Set `false` for local http dev or the login cookie won't be sent. Keep `true` in prod. Doubles as the dev/prod tell for the secret check above. |
| `PICLSTATS_ADMIN_EMAIL` / `PICLSTATS_ADMIN_PASSWORD` | Bootstrap admin: created on startup if no user with that email exists. |
| `PICLSTATS_RESEND_API_KEY` | Resend key for invite/reset emails. Blank = links are logged and shown in the admin UI instead of sent (fine for local dev). |
| `PICLSTATS_EMAIL_FROM` | Sender, e.g. `PICL Stats <noreply@yourdomain>`. Must be on a domain verified in Resend. |
| `PICLSTATS_PUBLIC_BASE_URL` | The site's one public name, e.g. `https://piclstats.com`. Emailed links use it, and GET/HEAD requests on any other host (`www.`, `piclstats.fly.dev`) 301 to it (`web/canonical.py`). Unset, links use the requesting host and nothing redirects. |
| `PICLSTATS_SCRAPE_DELAY_SECONDS` / `PICLSTATS_LOG_LEVEL` | Scraper politeness delay; log level. |

Then create/migrate the schema and load data:

```bash
piclstats init-db        # alembic upgrade head
piclstats seed           # reference data: courses, conferences, division profiles
piclstats scrape         # all seasons (or --season 2025, --event-id N, --dry-run)
piclstats merge auto     # dedupe riders by name (merge status / conflicts / link / unlink)
piclstats serve --reload # dashboard at http://localhost:8000
piclstats query stats    # quick sanity check (also: rider/team/event)
piclstats dq check --all # data-quality checks over every event (scrape runs them per event)
piclstats dq status      # results by dq_status + recent scrape runs
piclstats dq lineage     # rebuild the lineage log (merges and folds); seed and merge auto do this too
piclstats dq scorecard   # metrics + golden fixtures + publish gate vs the previous run
piclstats discover       # new races on pamtb.org/results-standings; --scrape loads them through the pipeline
```

### Data quality

Every scrape records a `scrape_runs` row and runs the checks in
`quality/checks.py` over that event: a clock time parsed as an elapsed time,
a total over the cutoff, a non-positive place, an unknown status, an OK finish
with no time, splits that do not add up (penalty-aware), bib 0, laps that
disagree with the division profile, place gaps. Findings land in `dq_checks`
and roll up into `results.dq_status`: `excluded` rows (any error) leave every
statistic, `warn` rows stay but show a badge on the rider page. Raw columns are
never rewritten. After a deploy that adds new checks, run
`piclstats dq check --all` once against production. Design: ADR 002.

**Nightly discovery.** `.github/workflows/nightly-discover.yml` runs
`piclstats discover --scrape` on the production app every morning. It reads
the league results page, records every raceresult link in `discovered_events`,
and loads anything new: parse → load → checks → scorecard → gate. A new race
is published (`events.is_published`) only if the gate passes; otherwise it
stays loaded but hidden, listed on `/admin/dq` with the reasons and a
"Publish anyway" button. Re-scrapes never change the flag. New ids need not
be in `scraper/registry.py`; the season is the calendar year and the order
follows the last loaded race.

Rider merging (`piclstats merge auto`) blocks on `riders.name_key`, so
punctuation and spacing variants of one name (O'REILLY / OREILLY) merge, while
two riders sharing a name who raced in the same event never do. Every merge
and every fold `seed` performs (division aliases, event types, course mapping,
conference groups, team spelling variants) is recorded as an edge in
`picl_lineage` with its mechanism (exact, casing, whitespace, punctuation,
alias, pattern, typo, manual); the admin DQ page renders that as a merge map.

## Web app

Public pages: `/` (home), `/leaderboard`, `/riders`, `/rider/{id}`, `/teams`, `/team/{name}`, `/courses`, `/course/{id}`, `/results` (published finish list per event and category, with a Race Position tab: position bump chart + lap-times Gantt with top-N filter; `/racechart` redirects there).

Login-gated (session cookie auth — see `web/auth.py`), by role:

| role | can use |
|---|---|
| `coach` | `/rider/{id}/forecast` (finish-time predictions), `/account` |
| `picl` | coach + `/staging` and `/staging.csv` (age-group z-score speed ratings, the seeding formula) |
| `admin` | everything + `/admin` |

Roles rank, so each includes the ones below it (`auth.role_allows`). Anyone signed in changes their own password at `/account`.

Admin-only: `/admin` (courses, forecast tuning, user management at `/admin/users`, data quality at `/admin/dq`: pipeline flow, scorecard trend, findings per check, scrape runs and gate, lineage inspector with merge map).

**Usage** (`/admin/usage`): a first-party page log (`page_views`, written off-thread by `web/usage.py`). No cookies and no third party: a visitor is `sha256(salt + date + ip + user agent)[:16]`, so uniques count per day without storing anything identifying; query strings keep allow-listed params only; bots and errors are excluded from counts; rows older than 180 days are pruned. Shows visitors and views, routes with p50/p95 latency, riders and teams looked up, coach activity, referrers, and the recent log.

### Course profiles

Pace, staging ratings, and forecasts divide a rider's time by laps × loop distance, so each course carries MS and HS loop distance and elevation gain plus a lap count per division and gender. Profiles are **per season**: a season-NULL default plus optional rows for each year, resolved season-first everywhere (`web/queries._lap_joins`). `piclstats seed` creates a row for every course-season with results, taking lap counts from what finishers actually recorded (the spreadsheet defaults were wrong at most venues — see `docs/decisions/001`). Seeding never overwrites rows, so edits made at `/admin/courses/{id}` stick. That page shows the recorded lap count beside each entry and highlights mismatches.

After a fresh deploy that adds this migration, run `piclstats seed` against production once to populate the season rows.

### Access model

Public pages need no account. Everything gated is **invite-only** — there is no signup page.

1. An admin invites an address at `/admin/users` and picks the role (default `coach`).
2. The app emails a one-time link (7-day expiry) and also shows it once on screen, so the admin can send it another way if email is down.
3. The coach opens `/invite/{token}`, sets their own password (12 chars minimum, passphrases encouraged), and lands signed in on the first page their role can use (`/staging` for picl and admin, the dashboard for coaches).

A password never passes through an admin. Forgotten passwords self-serve via `/forgot` → emailed link → `/reset/{token}` (1-hour expiry); admins can also trigger that email from `/admin/users`. Only the SHA-256 hash of each token is stored, links are one-time, and issuing a new one retires any outstanding link for that address.

`/forgot` returns the same response whether or not the address exists, so it can't be used to enumerate accounts. Failed logins are throttled per IP+account (5 in 15 minutes buys a 15-minute cooldown), reset requests more tightly (3). Both counters are in-process, so they need a shared store before running more than one machine.

## Checks

```bash
uv run pytest tests/ -q      # 98 tests, DB stubbed — no PostgreSQL needed
uv run ruff check .          # lint
uv run ruff format .         # format (line length 100)
uv run mypy                  # types; config in pyproject.toml
```

All four run in CI on every PR. Config lives in `pyproject.toml`.

## Backups

`scripts/backup_prod.sh [DEST_DIR]` dumps the production database through a temporary `fly proxy` with `pg_dump` (custom format) and keeps the newest 30 dumps (`KEEP=n` to change). Default destination is `~/Backups/piclstats`; point `DEST_DIR` or `PICLSTATS_BACKUP_DIR` at a TrueNAS share to keep copies off the laptop. Needs flyctl logged in and `pg_dump` from `brew install libpq`. Fly's daily volume snapshots (retained a few days) are the only other backup, so run this after each results load at minimum. Restore into an empty database with `pg_restore --no-owner --no-privileges -d "$URL" file.dump`.

The app sets a 15 s server-side `statement_timeout` on its connections (`PICLSTATS_STATEMENT_TIMEOUT_MS`, 0 disables) so one slow query fails one request rather than starving the small Postgres VM.

## Deployment

Push to `main` runs the CI checks and, only if they pass, auto-deploys via GitHub Actions (`.github/workflows/fly-deploy.yml` calls `ci.yml` as a required job; needs the `FLY_API_TOKEN` repo secret). `fly.toml`'s `release_command` runs `alembic upgrade head` before the new version serves traffic. Prod secrets are set with `flyctl secrets set`, not `.env`.

## Docs

- `docs/staging-and-dq-spec.md` — staging speed-rating (z-score) and data-quality spec
- `docs/decisions/` — architecture decision records (index in `DECISIONS.md`)
