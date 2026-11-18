# Architecture notes (Stage A)

Decisions live in `CLAUDE.md`; this file records implementation conventions.

## Sizing and positions
- **Whole shares only.** `qty = floor(max_position_value / price)`. `qty` columns are
  `NUMERIC(18,4)` for headroom, but the engine only ever writes whole numbers.
- **Positions are DERIVED from `paper_fills` and never stored.** Net position per
  instrument = sum of buy qty - sum of sell qty over fills. `paper_fills` is the single
  source of truth, so there is no second table that can drift from the ledger.

## Orders
- `paper_orders.origin` is `signal` or `eod_exit`. End-of-day forced exits have no
  MA-crossover behind them, so `signal_id` is NULL for them. A CHECK enforces
  `(origin = 'signal') = (signal_id IS NOT NULL)`.
- `paper_orders.strategy_run_id` links every order, including EOD exits, to its run.

## Currency
- Single-currency simulation. `strategy_runs.currency` is the run's currency; every
  instrument in a run must have that same `instruments.currency`, otherwise the run is
  rejected (must-pass test 8). **No FX conversion** is built or planned for V1.
- Defaults (CAD 1,000 start, CAD 200 cap) are numbers in the run's currency; for a USD
  run they are interpreted as USD.

## Data freshness
- `price_bars.ts` is the bar open time; `price_bars.ingested_at` is when we stored it.
  `ingested_at` backs the "no order on stale data" rule and the Watchlist data timestamp.
- Index `ix_price_bars_backtest (instrument_id, interval, ts)` matches the backtest query.

## Adapters
`PriceProvider` and `ComplianceProvider` live in `backend/app/providers/`. Only
`MockProvider` / `MockComplianceProvider` exist in Stage A. `ProviderError` means fail
loudly; never interpolate or reuse a last-known price.

## Auth
Supabase Auth owns users and sessions. The frontend signs in with supabase-js and sends
the access token as `Authorization: Bearer`. The backend only verifies the JWT
(`app/auth.py`): JWKS by default, or HS256 if `SUPABASE_JWT_SECRET` is set.
`strategy_runs.user_id` and `compliance_checks.created_by` hold the Supabase user UUID
with no foreign key, because `auth.users` is in a schema Alembic does not manage.
