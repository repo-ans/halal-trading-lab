# Handover — Stage A + B1 done (18 Nov 2026)

Read `CLAUDE.md` first (decisions, rejected options, legal constraints). This file
records what was built on top of it and what is still pending.

## Status
- **Stage A: done, live-verified 2026-11-18.** All 4 checks passed: `/health` via
  uvicorn direct (Docker skipped for this run), `alembic upgrade head` + live RLS test
  against the real Supabase project, `npm run build`, and frontend login + `/me` → 200
  with a real Supabase Auth session.
- **Stage B1 (backend data pipeline, no charts): done 2026-11-18, including two
  hardening passes and one real run against production.** See below.
- **Stage B2 (charts) is next; plan pending owner approval before any file is created.
  The rest of Stage B/C onward: not started.**

## What exists
```
.env.example  .gitignore  README.md  docker-compose.yml  (backend service only, no db)
docs/architecture.md  docs/HANDOVER.md
backend/  pyproject.toml  Dockerfile  alembic.ini  alembic/{env.py,versions/0001,0002}
  app/{main,config,db,auth,market_calendar,scheduler}.py
  app/models/   8 tables (instrument, compliance, price_bar, strategy[StrategyRun,Signal],
                paper[PaperOrder,PaperFill], ai_report)
  app/providers/price/{base,mock,yfinance_provider}.py   PriceProvider (+ symbol_for),
                Bar, ProviderError, MockProvider(fail=), YFinanceProvider (dev-only)
  app/providers/compliance/{base,mock}.py   MOCKA..MOCKD fixtures (fake, not rulings)
  app/seed/{__init__,instruments.json}   seed_instruments() -- instruments table only,
                never compliance_checks; AAPL/MSFT/KO (US) + RY (TSX .TO experiment)
  app/services/{validation,backfill}.py   pure validation rules, run_backfill()
  tests/  conftest.py (live_db_url/db_conn fixtures, local test-DB only),
          test_must_pass.py (7 strict xfails + 1 real test), test_mock_provider,
          test_schema_types, test_market_calendar, test_db_fixtures (RUN_DB_TESTS=1),
          test_validation, test_yfinance_provider, test_backfill_service
          (RUN_DB_TESTS=1), test_jobs_route
frontend/  React+Vite+TS+TanStack Query+supabase-js. Login page, /me call, Disclaimer.
```
Backend venv: `backend/.venv` (gitignored). Tests: `cd backend; .\.venv\Scripts\python.exe -m pytest`
→ 51 passed, 11 skipped (10 DB-writing + 1 live read-only RLS, both opt-in), 7 xfailed.
With `RUN_DB_TESTS=1` (needs `docker compose --profile test up -d db_test` first) and
`RUN_LIVE_RLS_CHECK=1`: 62 passed, 0 skipped, 7 xfailed. Frontend `npm run build` passes.

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

## Live verification notes (for reproducing this later)
- `.env` is filled (owner did this directly, contents never printed to the agent).
  `backend/.env` is a copy of root `.env` (pydantic-settings resolves `.env` relative
  to cwd, and uvicorn/pytest run from `backend/`); `frontend/.env.local` holds the
  `VITE_*` subset. Both are `.gitignore`d the same as root `.env`.
- Ran uvicorn directly (`./.venv/Scripts/python.exe -m uvicorn app.main:app`), not via
  `docker compose up` — Docker was skipped for this run at the owner's instruction.
- **Gotcha, now fixed (see below):** `RUN_DB_TESTS=1 pytest` originally connected to
  `localhost` with a dummy user instead of the real Supabase project, because
  `tests/conftest.py` unconditionally set a fallback `DATABASE_URL` env var, which
  outranks the `.env` file in pydantic-settings. Fixed by gating that fallback on
  `RUN_DB_TESTS != "1"`.

## Test-DB safety fix (18 Nov 2026, before Stage B1)
DB-writing tests must never be able to touch the production Supabase project. The
owner cannot create a second Supabase project, so this uses a throwaway **local**
Postgres instead of a second hosted project.
- `docker-compose.yml`: new `db_test` service (`postgres:17-alpine`, matching the
  production project's major version), under `profiles: ["test"]` so a plain
  `docker compose up` never starts it. tmpfs-backed (no volume — wiped on every
  restart), port 5433, db `halal_test`, user/password `postgres`/`postgres`. Start it
  with `docker compose --profile test up -d db_test`.
- Added `TEST_DATABASE_URL` setting (`backend/app/config.py`, `.env.example`),
  defaulting to that local service — nothing to configure in `.env` normally.
- `backend/tests/conftest.py`:
  - `live_db_url` fixture refuses (hard `pytest.fail`, never a silent skip) if
    `TEST_DATABASE_URL` is unset, equal to `DATABASE_URL`, **or its host isn't
    `localhost`/`127.0.0.1`** — the third guard is what makes it impossible to point
    tests at Supabase by accident, even a differently-named Supabase project.
  - `_migrated_test_db` (session-scoped) runs the real `alembic upgrade head` against
    the local test DB once per test run, so DB-writing tests exercise migrations 0001
    + 0002 for real, not an approximation.
  - `db_conn` fixture yields a `Session` joined to an outer transaction via
    `join_transaction_mode="create_savepoint"` (SQLAlchemy's documented recipe for
    this). The test's own `commit()` only closes a SAVEPOINT; the outer transaction is
    always rolled back at teardown, so no test can leave a row behind. **Use the
    Session-based recipe, not a hand-rolled version on a raw `Connection`** — a raw
    `Connection.commit()` does not respect the nested-savepoint boundary and will
    really commit, as a first draft of this fix discovered by leaking a row into
    `db_test` (cleaned up by hand; harmless since the DB is throwaway).
  - `alembic/env.py` had a latent bug this surfaced: it unconditionally overwrote
    `sqlalchemy.url` from `DATABASE_URL` on every run, so programmatically pointing
    `command.upgrade()` at a different database (the test DB) was silently ignored.
    Fixed to only default from `DATABASE_URL` when the caller hasn't already set a URL
    — CLI usage (`alembic upgrade head`) is unaffected.
- **The live RLS check stays on `DATABASE_URL` (production), not the test DB** — its
  job is specifically to verify the production project's RLS config, so redirecting it
  would defeat the point. Renamed to `test_rls_enabled_on_all_tables_live_readonly`,
  moved to its own marker `RUN_LIVE_RLS_CHECK=1` (separate from `RUN_DB_TESTS`, which
  now means "run the local-Postgres DB-writing tests"), and hardened: it issues
  `SET TRANSACTION READ ONLY` as the first statement (Postgres itself then rejects any
  write in that transaction) and is never committed, only closed.
- CLAUDE.md §2 updated: "no Postgres service for the app database" now documents the
  one exception (`db_test`, test-profile only).
- **Proven working, not just written:** `backend/tests/test_db_fixtures.py` runs real
  inserts through `db_conn` against `db_test` and independently re-connects to confirm
  nothing survives teardown — `RUN_DB_TESTS=1 pytest` → 3 passed (not skipped).
  `RUN_LIVE_RLS_CHECK=1 pytest` against the real Supabase project → 1 passed. Both
  together, plus the full suite: 22 passed, 8 xfailed, 0 skipped, 0 failed. All three
  refusal paths (unset / equal-to-prod / non-local host) verified by hand to fail
  loudly.

## Stage B1: backend data pipeline (18 Nov 2026, no charts)
CLAUDE.md §4's highest-priority action -- the daily backfill job -- is now running.
- **`YFinanceProvider`** (`app/providers/price/yfinance_provider.py`): dev-only, raises
  `ProviderError` in `__init__` unless `ALLOW_DEV_DATA_SOURCE=true`. Lazy-imports
  `yfinance` (never required just to import the module). Regular session only
  (`prepost=False`), raw unadjusted prices (`auto_adjust=False` -- deliberate: adjusted
  prices would make a refetched bar look "different" after any split, confusing the
  differs-but-kept warning for reasons that have nothing to do with a real error),
  `Decimal(str(float(x)))` throughout, UTC conversion, and raises `ProviderError` on
  any HTTP failure, empty response, or NaN (partial response).
- **`PriceProvider.symbol_for(symbol, exchange)`** (`base.py`): new, non-abstract,
  identity-default method so `MockProvider` needed zero changes. Keeps
  `Instrument.symbol`/`exchange` provider-agnostic (`RY`/`XTSE`); only
  `YFinanceProvider.symbol_for` knows the Yahoo suffix (`RY` + `XTSE` → `RY.TO`).
- **Bar-open timestamp assumption, now confirmed live** (not just by convention): a
  real call against `AAPL` and `RY.TO` for the same 5-day window returned 234 bars
  each (3 trading days × 78), both starting 14:30 UTC and ending 20:55 UTC -- the
  grid and session alignment match what `market_calendar.session_bounds` expects.
- **The `.TO` experiment answered a real question:** Yahoo DOES return 5-minute bars
  for `RY.TO` -- yfinance works for TSX intraday too, dev-only caveats aside. This is
  independent of CLAUDE.md §4's EODHD "probably, unverified" note; it doesn't resolve
  the §11 US-only-vs-TSX provider decision (commercial licensing cost is still the
  real question there), but it means the pipeline itself isn't the blocker.
- **`app/seed/instruments.json`:** AAPL/XNAS/USD, MSFT/XNAS/USD, KO/XNYS/USD,
  RY/XTSE/CAD. `seed_instruments()` upserts only the `instruments` table (ON CONFLICT
  on `symbol, exchange`) -- it never touches `compliance_checks`, so all four stay
  blocked from trading by default (CLAUDE.md §5) until a human reviews them.
- **`app/services/validation.py`:** pure functions, no DB/network --
  `validate_bar` (high>=low, open/close within [low,high], positive prices,
  non-negative volume, 5-minute grid, inside session bounds), `expected_bar_count`,
  `is_forming`. `app/market_calendar.py` gained an `lru_cache`d calendar lookup here
  too -- computing gaps over a 59-day window was reconstructing the full
  `pandas_market_calendars` holiday schedule on every single day (83s for 7 tests);
  caching the `Calendar` object (not the schedule query) dropped that to ~1s.
- **`app/services/backfill.py`:** `run_backfill(session, provider, instruments, now)`.
  Per-instrument try/except -- one symbol's `ProviderError` is recorded on that
  instrument's report and never blocks the others. Drops still-forming bars
  (`bar.ts + 5min > now`). Diffs against what's already stored by `ts`: new → insert,
  identical → no-op, **different → logged as a warning, original value kept, never
  overwritten**. Reports per-day gaps (actual vs. `expected_bar_count`) -- report,
  never repair.
- **`POST /jobs/backfill`** (`app/main.py`): `X-Job-Secret` header, constant-time
  compared (`hmac.compare_digest`); 503 if `JOB_SECRET` is unset (never "no auth
  required"); 401 on a missing/wrong header; runs the same `run_backfill_job()` the
  scheduler uses, via a FastAPI dependency so tests can override it without touching
  the network or a real DB.
- **`app/scheduler.py`:** `AsyncIOScheduler`, one daily cron job (02:00 UTC -- a
  generous buffer past any US/TSX close including DST, not a precise finalization
  calculation; revisit if a provider with documented finalization timing replaces
  yfinance). `app/main.py`'s lifespan starts it and fires one backfill in a background
  thread on startup (`asyncio.to_thread`, so `/health` doesn't block on it).
- **Must-pass test 6 replaced for real** (`test_must_pass.py`): a failing fake
  provider + a bare `MagicMock` session proves `run_backfill` never touches the
  session (no read, no write) once the provider raises -- not just that an exception
  propagates.
- **Tests, all fixture/mock-based for the provider (no real yfinance network calls in
  the suite)**, DB-writing ones via `db_conn`/`RUN_DB_TESTS=1` against the local
  `db_test` database: idempotency, each validation rule, forming-bar drop, differs-
  but-kept, per-instrument failure isolation, gap reporting, DST/half-day bar counts,
  the route's 503/401/200 paths. All of it is also proven once against the real
  network (the `.TO` check above) and once against the real local test DB (the
  `test_backfill_service.py` run below) -- not just written and left unexercised.
- **Not built yet (explicitly out of scope for B1):** charts, Celery/Redis (still
  rejected), any other price provider.

## B1 hardening pass + first live production run (18 Nov 2026)
Owner review of B1 required several additions before accepting it, then a real run.
- **`PriceProvider.symbol_for`/`YFinanceProvider` hardening:**
  `auto_adjust=False` and `repair=False` are now both passed explicitly (the latter
  was already yfinance's default, but relying on an upstream default for a
  never-invent-data guarantee was the wrong call) --
  `test_requests_unadjusted_unrepaired_regular_session_data` asserts the actual kwargs
  sent, not just that the code has a comment saying so.
  `test_returns_exact_expected_timestamp_set` checks an exact 3-bar timestamp set, not
  just a count. The bar-open-timestamp assumption is now a named, citable limitation:
  `test_close_indexed_timestamp_cannot_be_detected_by_validation`
  (`tests/test_validation.py`) proves a bar shifted +5 minutes (as if Yahoo indexed by
  close instead of open) still passes every validation check -- grid/session checks
  cannot catch an indexing-convention error, full stop.
- **`POST /jobs/backfill` now returns 207 (not 200) if any instrument has an error** --
  `test_any_instrument_error_returns_207`.
- **6-hour startup guard** (`app/scheduler.py::_recently_backfilled`): the lifespan
  startup trigger now skips if `price_bars.ingested_at` shows a backfill for this
  provider within the last 6 hours. Checked against the DB, not in-memory state, so it
  survives a process restart -- the exact case it protects against (every
  `uvicorn --reload` hot-reload would otherwise re-hit Yahoo for the full lookback
  window). The daily cron job is NOT guarded (it only ever fires once a day). Tests:
  `test_recently_backfilled_{true_within_window,false_when_stale,false_when_absent,
  ignores_other_sources}` (`tests/test_scheduler.py`).
- **25% close-to-close jump warning**, never a rejection (real stocks do gap that
  much): `InstrumentReport.large_jumps`, seeded from the closest already-stored bar so
  a jump across a run boundary is caught too, not just jumps within one fetch.
  `test_large_close_jump_is_flagged_but_still_inserted`,
  `test_small_close_move_is_not_flagged`.
- **`hmac.compare_digest`** was already in place from the original B1 pass
  (`app/main.py::trigger_backfill`) -- confirmed, not new. Its constant-time property
  isn't something a unit test can assert (timing isn't reliably measurable in CI); the
  pass/fail behavior it produces is covered by `test_wrong_secret_returns_401` and
  `test_missing_header_returns_401`.
- **`db_conn` rollback regression test rewritten to be order-independent.** The
  original version split the proof across two tests relying on pytest's default
  execution order -- silently uninformative if the second test ever ran alone.
  `conftest.py`'s rollback mechanism was extracted into a plain context manager,
  `transactional_session()` (the `db_conn` fixture is now a thin wrapper around it), so
  `test_db_conn_regression_rolls_back_after_fixture_exits`
  (`tests/test_db_fixtures.py`) can drive it directly inside one test: insert a
  uniquely-named row, commit, exit the `with` block, then check from a **separate**
  fresh connection that it's gone. Verified this actually catches a regression: briefly
  reverted `transactional_session` to the original (broken) raw-`Connection` version by
  hand, confirmed the test failed with a real leaked row, then restored the correct
  version and cleaned up the leaked row.
- **Real bug found and fixed during the live production run, not in testing:**
  the very first idempotency check against production showed ~100% of refetched bars
  as "differs from stored value" instead of "duplicate" -- alarming, since that
  warning means "something about this historical bar changed," which shouldn't happen
  for bars from two months ago. Root cause: yfinance's underlying float values carry
  noise past 4 decimal places (observed live: `334.5411071777344` for a bar a
  `NUMERIC(18,4)` column stores as `334.5411`), so a freshly-converted Decimal never
  exactly equalled the DB-rounded stored Decimal, even when the "real" price hadn't
  changed at all. **No data was corrupted** -- differs-but-kept never overwrites, it
  only warns and keeps the original value -- but the warning itself was meaningless
  noise on every single bar of every rerun. Fixed by quantizing to `NUMERIC(18,4)`'s 4
  decimal places immediately in `YFinanceProvider.get_bars`, not just at insert time.
  Regression test: `test_quantizes_prices_to_four_decimal_places`, using the exact
  real-world float value above. Re-ran the full idempotency check after the fix; now
  exact (see below).
- **Live production run (real Supabase project, real yfinance network calls, real
  writes to `price_bars`):**
  - `seed_instruments()`: 4/4 upserted (idempotent on `symbol, exchange`).
  - First backfill run (3,276 = 42 trading days × 78 bars for the 3 US symbols;
    3,198 = 41 days × 78 for RY -- one fewer TSX trading day than NYSE/NASDAQ in the
    same window, almost certainly Canadian Thanksgiving, 2026-10-12, which isn't a US
    market holiday): all 4 instruments — 0 rejected, 0 errors, 0 large jumps.
    `min(ts)`=2026-09-21 13:30 UTC, `max(ts)`=2026-11-17 20:55 UTC for all 4. Gap
    report correctly flagged today (2026-11-18) as incomplete (`expected=78,
    actual=0`) since the run happened before/without today's session completing --
    report, don't repair, working as designed, not a bug.
  - Full-day sample, 2026-11-16 (Monday), confirming session alignment for both a US
    and the TSX symbol: **AAPL** first bar `2026-11-16T14:30:00+00:00` UTC =
    `09:30:00-05:00` ET (= session open), last bar `2026-11-16T20:55:00+00:00` UTC =
    `15:55:00-05:00` ET (= session close `16:00` minus 5 minutes). **RY** identical:
    first `14:30:00+00:00` UTC = `09:30:00-05:00` (America/Toronto, same UTC offset as
    ET that day), last `20:55:00+00:00` UTC = `15:55:00-05:00`.
  - Before the quantize fix: rerun showed `inserted=0` (correct) but `differs_kept` ≈
    100% of fetched bars (the bug above) instead of `duplicate`.
  - After the fix: reran a second time immediately -- `inserted=0`, `differs_kept=0`,
    `duplicate=fetched` **exactly**, for all 4 instruments. True idempotency confirmed
    against production, not just the local test DB.
- `db_test` (the local test Postgres) has been **stopped and removed**
  (`docker compose --profile test down`) per the owner's instruction -- it's
  tmpfs-backed with no volume, so nothing persists; `docker compose --profile test up
  -d db_test` recreates it fresh whenever `RUN_DB_TESTS=1` is needed again.
- **`ALLOW_DEV_DATA_SOURCE=true` and a freshly generated `JOB_SECRET` are now set in
  both `.env` and `backend/.env`**, per the owner's instruction, to run the collector
  for real. Left on (not reverted) -- the owner didn't ask for it to be turned back
  off. Neither value was ever printed; `JOB_SECRET` was generated with
  `secrets.token_urlsafe(32)` and written directly to both files.
- The `uvicorn`/`vite` dev servers started earlier in this session are still running,
  but with the **old** cached settings from before this round's `.env` changes
  (`ALLOW_DEV_DATA_SOURCE`/`JOB_SECRET` are read once at process start via
  `lru_cache`) -- restart the backend process if you want it to pick up the new values.

## B1 FIX 1 + FIX 2 (18 Nov 2026, second review round)
- **FIX 1 — `POST /jobs/backfill` now returns 502, not 207, when any instrument's
  fetch failed.** 207 is a 2xx; `curl --fail` and GitHub Actions treat any 2xx as
  success, so a cron would have stayed green through a real upstream failure. Also
  added: `InstrumentReport.recent_session_gap_pct` -- the gap ratio of only the most
  recent **completed** session (`session_close <= now`), not the whole 59-day window.
  502 also fires if that exceeds 10%. Gaps in older, already-permanent sessions are
  excluded on purpose (`test_gap_in_older_not_most_recent_session_does_not_fail_the_
  cron`) -- a cron must not stay red forever over data that can never be recovered.
  `_compute_gaps` now returns `(gaps, recent_session_gap_pct)` together, computed in
  the same scan, rather than a second query. Tests:
  `test_any_instrument_error_returns_502`,
  `test_large_recent_session_gap_returns_502_even_without_a_fetch_error`,
  `test_small_recent_session_gap_stays_200`,
  `test_gap_in_older_not_most_recent_session_does_not_fail_the_cron`.
- **FIX 2 — set-level close-indexing detection.** `test_close_indexed_timestamp_
  cannot_be_detected_per_bar` (renamed) still holds for a single interior bar, but the
  day's *boundary* bars leak the convention: if bars were really close-indexed, the
  day's first real bar would show up at `session_open + 5min` (never `session_open`
  itself) and the last at exactly `session_close` (which `validate_bar` always
  rejects, but that rejection alone is easy to miss among routine ones).
  `detect_close_indexed_days()` (`app/services/validation.py`) checks the raw fetched
  set directly: no bar at `session_open` AND a bar at exactly `session_close`, same
  day. `InstrumentReport.suspected_close_indexed_days` surfaces it in the report; an
  `ERROR`-level log fires per suspected day. Does NOT affect the 502 decision (FIX 1
  and FIX 2 are independent per the request) -- it's a loud flag, not a cron failure,
  since the owner still needs to investigate rather than have the pipeline guess.
  Tests: `test_detects_close_indexed_day`, `test_open_indexed_day_not_flagged`,
  `test_missing_open_alone_is_not_enough_to_flag` (pure, `test_validation.py`), plus
  end-to-end through `run_backfill`:
  `test_suspected_close_indexed_day_flows_through_run_backfill`,
  `test_open_indexed_day_not_suspected` (`test_backfill_service.py`).
- `README.md` gained env var docs for `TEST_DATABASE_URL`/`ALLOW_DEV_DATA_SOURCE`/
  `JOB_SECRET`, a DB-writing-tests runbook, and an "Adding instruments / symbols"
  section (edit `app/seed/instruments.json`, re-seed via `/jobs/backfill` or a short
  `seed_instruments()` script; new symbols get no compliance record and stay blocked
  until Stage C, or a direct `compliance_checks` row, adds one).
- Full suite, both markers: 81 passed, 7 xfailed, 0 skipped, 0 failed.

## Open items / notes
- `npm audit`: 2 findings (esbuild/vite 5.x, dev-server only). Fix = major vite upgrade;
  not done, owner has not decided.
- Not a git repo yet (no commits made).
- CLAUDE.md §11 open decisions (US-only vs TSX, personal vs commercial licence,
  live suggestions vs backtest) are still unresolved and gate the provider choice --
  unaffected by the `.TO` pipeline confirmation above, which is a dev-only finding.
- CLAUDE.md §3 Stage H checklist item added: purge `price_bars` rows with
  `source='yfinance'` before any non-owner user logs in. There are now real such rows
  in production (13,026 bars total across 4 instruments as of this run: 3,276 each for
  AAPL/MSFT/KO, 3,198 for RY) -- this checklist item is no longer hypothetical.
