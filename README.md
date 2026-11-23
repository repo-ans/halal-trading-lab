# Halal Trading Lab

Practise day trading with **virtual money** on a human-approved halal watchlist.
Paper trading only. Not financial advice. Halal status comes from a third party, not
from this app. See [CLAUDE.md](CLAUDE.md) for scope and decisions.

Status: **Stage A through G done** (scaffold, schema, auth, backend data pipeline,
instruments/bars API, chart UI, compliance gate + watchlist, 5/20 MA signal engine,
paper-trading engine, Strategy Lab / Virtual Trades / Dashboard, AI-generated run
summaries). See [docs/HANDOVER.md](docs/HANDOVER.md) for details.

**AI summary disclosure**: the Dashboard's AI summary panel offers two modes.
*Template mode* (default) is automatic and sends nothing anywhere. *LLM mode*
sends only the already-computed run metrics (trade count, P&L, fees, etc. --
never raw price data) to an external AI provider to turn into prose; this is
shown next to the mode selector itself, not just documented here.

## Prerequisites
- Python 3.12 (also developed and tested on 3.14 this far; `pyproject.toml` only
  requires `>=3.12`, so either works), Node 20+, Docker (optional)
- A Supabase project (provides Postgres and Auth)

## Environment variables
Copy `.env.example` to **both** `.env` at the repo root (`docker-compose.yml` needs
this to exist even for `db_test`-only commands, which otherwise don't touch any
secret) **and** `backend/.env` (every backend command is run from inside
`backend/`, and `app/config.py` reads `.env` relative to the current directory --
the repo-root copy alone is not enough once you `cd backend`). Also copy the
`VITE_*` values to `frontend/.env.local`. Never commit real keys.

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | backend | Supabase Postgres, `postgresql+psycopg://...` |
| `SUPABASE_URL` | backend | Project URL, used to fetch JWKS to verify tokens |
| `SUPABASE_JWT_SECRET` | backend | Optional; only for legacy HS256 projects |
| `CORS_ORIGINS` | backend | Allowed browser origins, comma-separated |
| `APP_ENV` | backend | `development` / `production` |
| `APP_MODE` | backend | `production` (default) / `demo` -- see "Demo environment" below |
| `TEST_DATABASE_URL` | backend tests | Local throwaway Postgres for DB-writing tests; leaving it blank (as `.env.example` ships it) or omitting it entirely both correctly fall back to `db_test` below -- `app/config.py` treats an empty value the same as unset |
| `ALLOW_DEV_DATA_SOURCE` | backend | Must be `true` for `YFinanceProvider` to run at all (dev-only, CLAUDE.md §3) |
| `JOB_SECRET` | backend | Shared secret for `POST /jobs/backfill`'s `X-Job-Secret` header; empty disables the endpoint (503) |
| `ADMIN_USER_IDS` | backend | Comma-separated Supabase user UUIDs allowed to write compliance records; empty/unset means no one is admin |
| `ALLOWED_USER_IDS` | backend | Stage H. Comma-separated Supabase user UUIDs allowed to use the app at all; empty/unset means no one is allowed. `ADMIN_USER_IDS` members count as allowed automatically |
| `OWNER_USER_IDS` | backend | Stage H licence guard. Comma-separated Supabase user UUIDs allowed to see yfinance-sourced price data; empty/unset means no one, not even the real owner, can see it (fail closed) |
| `AI_API_KEY` | backend | Anthropic API key for Stage G's LLM summary mode; empty disables LLM mode (template mode still works) |
| `AI_MODEL` | backend | Model id for LLM summaries, default `claude-haiku-5-5` |
| `AI_SUMMARY_DAILY_CAP` | backend | Per-user rolling-24h cap on LLM attempts, default 5 |
| `ZOYA_API_KEY` | backend | Zoya Finance API key (sandbox only so far); empty disables the admin-only Zoya lookup (503). Lookup-only -- never writes a compliance record by itself |
| `ZOYA_API_URL` | backend | Defaults to Zoya's sandbox endpoint (randomized dummy data); switch to the live endpoint only once a live key exists |
| `ZOYA_TIMEOUT_SECONDS` | backend | Per-call timeout for the Zoya lookup request, default 10 |
| `VITE_SUPABASE_URL` | frontend | Same project URL |
| `VITE_SUPABASE_ANON_KEY` | frontend | Public anon key |
| `VITE_API_URL` | frontend | Backend base URL |

Create the owner login in the Supabase dashboard (Authentication > Users). The app has
no sign-up screen.

**Stage H: disable public sign-ups too.** `ALLOWED_USER_IDS`/`ADMIN_USER_IDS` stop an
unauthorized Supabase user from using the app's API, but a stranger could still create
a Supabase Auth account in the first place if sign-ups are left on. In the Supabase
dashboard, go to Authentication > Providers (or Authentication > Settings, depending on
project version) and disable email/sign-up for new users -- the owner account is
created directly in the dashboard (above), not through a sign-up flow, so this never
blocks the owner.

**Stage H: rate limiting is single-instance only.** `POST /strategy-runs` and
`POST /strategy-runs/{id}/paper-run` are rate-limited per user (`app/rate_limit.py`),
but the limiter's state lives in the backend process' own memory, not a shared store
like Redis. Running more than one backend instance behind a load balancer gives each
instance its own independent counter, so the effective limit becomes
`max_requests * instance_count`, not a shared cap. Fine for this app's current
single-instance deployment (Render free tier, one container); revisit if that changes.

## Run the backend
With Docker (backend container only; the database is Supabase):
```
docker compose up --build
```
Or locally:
```
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload
```
Health check: http://localhost:8000/health

## Migrations
```
cd backend
alembic upgrade head        # apply
alembic downgrade base      # revert
alembic upgrade head --sql  # print SQL without a database
```
In Docker: `docker compose run --rm backend alembic upgrade head`

## Run the frontend
```
cd frontend
npm install
npm run dev
```
http://localhost:5173

**Stage H: never pass `--host` to `npm run dev`.** The dev server binds to localhost
only by default, which is the mitigation for `npm audit`'s esbuild finding ("esbuild
enables any website to send any requests to the development server and read the
response") -- adding `--host` to expose it on the LAN would remove that protection.
See `docs/RELEASE_CHECKLIST.md` for the full dependency scan results.

## Tests
```
cd backend
pytest
```
All 8 of CLAUDE.md section 9's must-pass tests are real implementations (no `xfail`
placeholders remain, as of Stage H) -- everything in the suite must pass.

DB-writing tests (idempotency, rollback regression, etc.) are skipped unless
`RUN_DB_TESTS=1`, and run against a throwaway **local** Postgres, never the production
Supabase project:
```
docker compose --profile test up -d db_test   # once; tmpfs-backed, port 5433
cd backend
RUN_DB_TESTS=1 pytest
docker compose --profile test down            # when done; nothing persists anyway
```
A separate, strictly read-only check against the real production project's RLS
configuration runs only with `RUN_LIVE_RLS_CHECK=1` (needs a real `.env`).

## Adding instruments / symbols
Instruments are seeded from `backend/app/seed/instruments.json`, never hardcoded
(CLAUDE.md §4). Each entry:
```json
{"symbol": "AAPL", "exchange": "XNAS", "currency": "USD", "name": "Apple Inc."}
```
- `exchange` is the MIC code. Currently supported (both `YFinanceProvider`'s ticker
  mapping and the market-calendar mapping need an entry for any new exchange):
  `XNAS`/`XNYS` (US, no ticker suffix) and `XTSE` (Toronto, `.TO` suffix). Adding a
  new exchange means adding it to `_EXCHANGE_SUFFIX` in
  `app/providers/price/yfinance_provider.py` and `EXCHANGE_CALENDARS` in
  `app/market_calendar.py` first.
- To add symbols: edit `instruments.json` (append entries, don't remove existing
  ones), then re-seed. Seeding is idempotent (upserts on `symbol, exchange`) and
  touches **only** the `instruments` table.

To re-seed and backfill the new symbols in one step, call the backfill endpoint (it
seeds before every run):
```
curl -X POST http://localhost:8000/jobs/backfill -H "X-Job-Secret: $JOB_SECRET"
```
To seed without backfilling (e.g. to stage symbols before turning on
`ALLOW_DEV_DATA_SOURCE`), run from `backend/`:
```python
from app.db import session_scope
from app.seed import seed_instruments

with session_scope() as session:
    seed_instruments(session)
```

**Every newly seeded symbol gets NO compliance record.** `seed_instruments()`
deliberately never touches `compliance_checks` -- a missing record means the paper
engine refuses to trade it (CLAUDE.md §5, a must-pass test). A new symbol, including
from the CEO's candidate list, stays blocked until a human adds a compliance record
through the Stage C admin flow (not built yet) or directly in `compliance_checks`.

## Demo environment

An isolated local database, separate from Supabase, so Stages E-G can be built and
demoed end-to-end without ever writing a fixture compliance record to production.
It is seeded with the real instruments (AAPL/MSFT/KO/RY -- these get **no**
compliance record, so they show "no_record") plus 6 synthetic `DEMO-*`
instruments, each carrying one arbitrary, clearly-labelled fixture status that
exercises every compliance gate branch. See CLAUDE.md for the exact mapping and
why no real company is ever assigned a fixture status.

`app/demo_mode.py` enforces, in both directions, that `APP_MODE=demo` can only ever
point at `localhost`/`127.0.0.1`:`5434`/`halal_demo`, and that any other `APP_MODE`
refuses to run against a database literally named `halal_demo`. The backend server
and `scripts/seed_demo.py` both call the exact same function.

**Start it (PowerShell):**
```powershell
# 1. Start the demo database (persists across restarts -- a named volume, not tmpfs)
docker compose --profile demo up -d db_demo

# 2. Seed it once (migrates, seeds real instruments with no compliance record,
#    creates the 6 DEMO-* fixtures, copies bars from production read-only).
#    SOURCE_DATABASE_URL is production's DATABASE_URL -- set it, or leave it unset
#    to be prompted (hidden input, never a CLI argument / shell history).
cd backend
$env:APP_MODE = "demo"
$env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5434/halal_demo"  # not "localhost" -- it resolves to the IPv6 loopback first here, which stalls every connection for a while before falling back
$env:SOURCE_DATABASE_URL = "<production DATABASE_URL>"   # optional; omit to be prompted
python scripts/seed_demo.py

# 3. Run the backend against the demo database (same env vars -- they override
#    .env, which is why step 2's $env: assignments are enough on their own)
uvicorn app.main:app --reload

# 4. Frontend, same as usual, pointed at this backend
cd ../frontend
npm run dev
```
Every screen shows a red "DEMO ENVIRONMENT" banner and `[DEMO]` in the browser tab
title (`GET /config` -> `{"app_mode": "demo"}`, checked before sign-in too). The
scheduler and the startup/`/jobs/backfill` collector are both disabled in demo mode
-- the data is a static, hand-seeded fixture, there is nothing to re-fetch.

**Fixture dates decay.** `DEMO-SOON`/`DEMO-EXPIRED`'s expiry dates are computed
relative to "now" at the moment `seed_demo.py` runs, so "expires within 14 days"
and "already expired" will eventually drift (e.g. `DEMO-SOON` will itself become
expired after enough real days pass). Re-run `seed_demo.py` to refresh them.

**After pulling a new migration**, `db_demo` needs it too -- it does NOT follow
production automatically, since `seed_demo.py` only runs `alembic upgrade head`
at seed time. Either run the migration directly against it:
```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5434/halal_demo"
cd backend
alembic upgrade head
```
or just re-run `seed_demo.py` (step 2 above), which does this as its first step
anyway.

**Resetting the demo database** (scoped -- never `docker compose down -v`, which
would remove every volume in this compose file, not just this one):
```powershell
docker compose --profile demo stop db_demo
docker compose --profile demo rm -f db_demo
docker volume rm halal-trading-lab_demo_pgdata
docker compose --profile demo up -d db_demo   # fresh, empty -- re-run seed_demo.py
```
