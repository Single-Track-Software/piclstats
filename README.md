# PICL Stats

Race-results scraper and analytics dashboard for the PA Interscholastic Cycling League (PICL / PAMTB). Scrapes results from raceresult.com into PostgreSQL and serves a FastAPI web dashboard with leaderboards, rider/team/course pages, race-position charts, staging speed-ratings, and finish-time forecasts.

**Production:** https://piclstats.fly.dev (Fly.io app `piclstats`, region `ord`, scale-to-zero — first request after idle takes a few seconds).

## Stack

- Python 3.11+, FastAPI + Jinja2 templates, uvicorn
- PostgreSQL via SQLAlchemy Core + Alembic migrations (prod DB is Fly Postgres)
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
| `PICLSTATS_PUBLIC_BASE_URL` | Absolute base for emailed links, e.g. `https://piclstats.fly.dev`. Unset, links use the requesting host. |
| `PICLSTATS_SCRAPE_DELAY_SECONDS` / `PICLSTATS_LOG_LEVEL` | Scraper politeness delay; log level. |

Then create/migrate the schema and load data:

```bash
piclstats init-db        # alembic upgrade head
piclstats seed           # reference data: courses, conferences, division profiles
piclstats scrape         # all seasons (or --season 2025, --event-id N, --dry-run)
piclstats merge auto     # dedupe riders by name (merge status / conflicts / link / unlink)
piclstats serve --reload # dashboard at http://localhost:8000
piclstats query stats    # quick sanity check (also: rider/team/event)
```

## Web app

Public pages: `/` (home), `/leaderboard`, `/riders`, `/rider/{id}`, `/teams`, `/team/{name}`, `/courses`, `/course/{id}`.

Login-gated (member or admin role, session cookie auth — see `web/auth.py`): `/staging` + `/staging.csv` (age-group z-score speed ratings, the seeding formula), `/racechart` (position bump chart + lap-times Gantt with top-N filter), `/rider/{id}/forecast`.

Admin-only: `/admin` (courses, forecast tuning, user management at `/admin/users`).

### Access model

Public pages need no account. Everything gated is **invite-only** — there is no signup page.

1. An admin invites an address at `/admin/users` and picks the role.
2. The app emails a one-time link (7-day expiry) and also shows it once on screen, so the admin can send it another way if email is down.
3. The coach opens `/invite/{token}`, sets their own password (12 chars minimum, passphrases encouraged), and lands signed in on `/staging`.

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

## Deployment

Push to `main` runs the CI checks and, only if they pass, auto-deploys via GitHub Actions (`.github/workflows/fly-deploy.yml` calls `ci.yml` as a required job; needs the `FLY_API_TOKEN` repo secret). `fly.toml`'s `release_command` runs `alembic upgrade head` before the new version serves traffic. Prod secrets are set with `flyctl secrets set`, not `.env`.

## Docs

- `docs/staging-and-dq-spec.md` — staging speed-rating (z-score) and data-quality spec
