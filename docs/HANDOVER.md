# Handover — end of Stage A (18 Nov 2026)

Read `CLAUDE.md` first (decisions, rejected options, legal constraints). This file
records what was built on top of it and what is still pending.

## Status
- **Stage A: built, locally tested. Live verification NOT finished.**
- **Stage B must not start until the 4 live checks below pass** (owner's instruction).

## What exists
```
.env.example  .gitignore  README.md  docker-compose.yml  (backend service only, no db)
docs/architecture.md  docs/HANDOVER.md
backend/  pyproject.toml  Dockerfile  alembic.ini  alembic/{env.py,versions/0001,0002}
  app/{main,config,db,auth,market_calendar}.py
  app/models/   8 tables (instrument, compliance, price_bar, strategy[StrategyRun,Signal],
                paper[PaperOrder,PaperFill], ai_report)
  app/providers/price/{base,mock}.py   PriceProvider, Bar, ProviderError, MockProvider(fail=)
  app/providers/compliance/{base,mock}.py   MOCKA..MOCKD fixtures (fake, not rulings)
  tests/  test_must_pass.py (8 strict xfails), test_mock_provider, test_schema_types,
          test_market_calendar
frontend/  React+Vite+TS+TanStack Query+supabase-js. Login page, /me call, Disclaimer.
```
Backend venv: `backend/.venv` (gitignored). Tests: `cd backend; .\.venv\Scripts\python.exe -m pytest`
→ 18 passed, 1 skipped (live RLS test), 8 xfailed. Frontend `npm run build` passes.

## Decisions made on top of CLAUDE.md (owner-approved)
1. `paper_orders.signal_id` nullable; `origin` = `signal`|`eod_exit`; CHECK
   `(origin='signal') = (signal_id IS NOT NULL)`; `paper_orders.strategy_run_id` NOT NULL FK.
2. **Single-currency simulation.** `strategy_runs.currency CHAR(3)`; all instruments must match;
   **no FX**. Must-pass test **#8** added (mixed currency → run rejected).
3. `price_bars.ingested_at` (stale-data rule, Watchlist timestamp) + index
   `(instrument_id, interval, ts)`.
4. Whole shares only: `qty = floor(max_position / price)`. **Positions are derived from
   `paper_fills`, never stored.**
5. Migration 0002: `ENABLE ROW LEVEL SECURITY` on all 8 tables, **no policies** (anon key
   ships in the frontend; backend uses direct Postgres and bypasses RLS). Currency CHECK
   `~ '^[A-Z]{3}$'` on instruments and strategy_runs.
6. Session open/close come **only** from `pandas_market_calendars` (`app/market_calendar.py`).
   `MockProvider` hardcodes 14:30 UTC (winter only) on purpose for determinism; a test
   forbids that constant outside `mock.py`. Verified: NYSE summer 13:30–20:00 UTC; half day
   2027-11-26 closes 18:00 UTC.
7. Auth: Supabase Auth only. Backend verifies JWT via JWKS (`SUPABASE_URL`), or HS256 if
   `SUPABASE_JWT_SECRET` is set. No sign-up screen; owner user created in the dashboard.
8. `user_id` / `created_by` are plain UUIDs, no FK (auth.users is not Alembic-managed).
9. `lightweight-charts` deliberately NOT added yet — add in Stage B.
10. `ai_reports.metrics` JSONB: serialise Decimals as strings, not floats.

## PENDING — live verification (blocked on credentials, not on code)
No `.env` exists. Docker daemon was not running. Nothing has touched real Supabase.
1. Start Docker Desktop; copy `.env.example` → `.env`, fill `DATABASE_URL`, `SUPABASE_URL`,
   (`SUPABASE_JWT_SECRET` only if legacy HS256), `VITE_*` (also `frontend/.env.local`).
2. `DATABASE_URL`: direct (5432) or **session-mode pooler (5432)**. **NOT port 6543.**
   Free-tier direct host is IPv6-only → use session pooler (`postgres.<ref>` user) if needed.
3. Checks, in order: `docker compose up` (backend only) → `alembic upgrade head` on Supabase
   + `RUN_DB_TESTS=1 pytest` (live RLS test) → frontend login with owner account →
   `GET /me` returns the verified user.
4. Report what breaks. Do not start Stage B until all four succeed.

## Open items / notes
- `npm audit`: 2 findings (esbuild/vite 5.x, dev-server only). Fix = major vite upgrade;
  not done, owner has not decided.
- Not a git repo yet (no commits made).
- CLAUDE.md §11 open decisions (US-only vs TSX, personal vs commercial licence,
  live suggestions vs backtest) are still unresolved and gate the provider choice.
- 🔴 CLAUDE.md §4: the daily 5-min backfill job should start as early as possible in Stage B.
