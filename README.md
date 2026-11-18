# Halal Trading Lab

Practise day trading with **virtual money** on a human-approved halal watchlist.
Paper trading only. Not financial advice. Halal status comes from a third party, not
from this app. See [CLAUDE.md](CLAUDE.md) for scope and decisions.

Status: **Stage A + B done** (scaffold, schema, auth, backend data pipeline,
instruments/bars API, chart UI). See [docs/HANDOVER.md](docs/HANDOVER.md) for details.

## Prerequisites
- Python 3.12, Node 20+, Docker (optional)
- A Supabase project (provides Postgres and Auth)

## Environment variables
Copy `.env.example` to `.env` at the repo root, and the `VITE_*` values to
`frontend/.env.local`. Never commit real keys.

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | backend | Supabase Postgres, `postgresql+psycopg://...` |
| `SUPABASE_URL` | backend | Project URL, used to fetch JWKS to verify tokens |
| `SUPABASE_JWT_SECRET` | backend | Optional; only for legacy HS256 projects |
| `CORS_ORIGINS` | backend | Allowed browser origins, comma-separated |
| `APP_ENV` | backend | `development` / `production` |
| `TEST_DATABASE_URL` | backend tests | Local throwaway Postgres for DB-writing tests; defaults to `db_test` below, usually leave unset |
| `ALLOW_DEV_DATA_SOURCE` | backend | Must be `true` for `YFinanceProvider` to run at all (dev-only, CLAUDE.md §3) |
| `JOB_SECRET` | backend | Shared secret for `POST /jobs/backfill`'s `X-Job-Secret` header; empty disables the endpoint (503) |
| `VITE_SUPABASE_URL` | frontend | Same project URL |
| `VITE_SUPABASE_ANON_KEY` | frontend | Public anon key |
| `VITE_API_URL` | frontend | Backend base URL |

Create the owner login in the Supabase dashboard (Authentication > Users). The app has
no sign-up screen.

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

## Tests
```
cd backend
pytest
```
The must-pass tests not yet implemented are strict-xfail placeholders until their
stage lands; they show as `xfailed`. Everything else must pass.

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
