# Halal Trading Lab — Project Context

> Handoff file. Read this before writing code. It records decisions already made,
> options already rejected (and why), and the legal constraints that are easy to get
> wrong. Do not re-derive these. If you need to change a decision here, say so
> explicitly and explain why.
>
> Source of truth for requirements: `Halal Trading Lab — MVP Developer Plan v1.0`
> (15 Nov 2026). This file records decisions made *on top of* that plan.
> Last updated: 18 Nov 2026.

---

## 1. What this is

A web app where a user practises **day trading with virtual money** on real market
prices, restricted to a human-approved **halal** watchlist.

- Starting virtual balance: **CAD $1,000**
- No real orders, no brokerage connection, no client funds — ever, in V1
- Losses must be reported as clearly and prominently as gains
- The app gives information and simulated fills; it does **not** execute real trades

### Hard out-of-scope for V1 (do not build, do not scaffold "for later")
- Brokerage connection, live orders, handling client funds
- AI-generated halal rulings (screening comes from a human + Zoya/Musaffa evidence)
- Leverage, shorting, options, margin, crypto
- Profit claims, subscriptions, public trade signals
- Any link to the owner's real retirement portfolio or Wealthsimple credentials

---

## 2. Tech stack (decided)

| Layer | Choice |
|---|---|
| Backend | Python 3.12 + FastAPI + Pandas + SQLAlchemy + Alembic |
| Scheduler | **APScheduler in-process** + GitHub Actions cron as external trigger |
| Market hours | `pandas_market_calendars` (library, offline, no API, no key) |
| DB + Auth | **Supabase** (PostgreSQL + Supabase Auth) |
| Money type | Postgres `NUMERIC(18,4)` + Python `Decimal` |
| Frontend | React + Vite + TypeScript + TanStack Query |
| Charts | **TradingView Lightweight Charts only** |
| Frontend host | Netlify (free) |
| Backend host | localhost via Docker Compose first; Render free tier when deployed |
| CI + cron | GitHub Actions |
| Tests | pytest + pytest-asyncio |

**No Postgres service for the app database — one exception.** The app always talks to
Supabase. The only local Postgres in this repo is `db_test` in `docker-compose.yml`,
under `profiles: ["test"]` (so a plain `docker compose up` never starts it): a
throwaway, tmpfs-backed database that DB-writing tests run against, so a test
`TRUNCATE` can never land on production data. See §9 and `backend/tests/conftest.py`.

### Explicitly rejected — do not add these
- **Celery + Redis** — the workload is one scheduled job per day, not a job queue.
  APScheduler covers it with zero extra infrastructure.
- **Recharts** — no native candlestick type; would need a custom-shape hack.
  Lightweight Charts does candlestick + line + histogram, so one library covers
  every chart in the app.
- **Render Postgres** — its free database expires after 30 days. The database is
  Supabase. Render runs the backend container only.
- **Fly.io / Koyeb** — no usable free tier for new accounts as of 2026.
- A separate hand-rolled auth system — Supabase Auth is already there.

### Deferred, not rejected
- LLM for Stage G summaries. Build stages A–F without it.

---

## 3. ⚠️ Data licensing — read this before touching any provider

This is the single most important non-obvious constraint in the project. Getting it
wrong creates legal exposure for Aniya Network Solutions Inc., not just a bug.

**"Publicly viewable" is not "licensed for use in our product."**

Market data providers price by *who sees the data*, not by how much you pull:

- **Personal / non-professional licence** = an individual using data for their own
  private investing. Explicitly excludes anyone acting for a business, and excludes
  showing the data to end users.
- **Commercial licence** = a company using data in a product or displaying it to
  users. Typically 10–40× the price for identical data.

Consequences for this project:

1. **Yahoo Finance / `yfinance` is development-only.** The yfinance docs state the
   Yahoo API is intended for personal use only, and Yahoo's terms bar using the API
   to earn income without written permission. Use it to validate the pipeline; do
   **not** ship it as the data source for real users.
2. Before any non-owner user can log in and see prices, a commercial data licence
   must be in place. Flag this at Stage H; do not quietly skip it.
3. The owner pilot (virtual funds, single user, no clients) is a grey area. Whether
   the provider treats it as personal or commercial is an **open question to ask the
   provider in writing** — the answer is the difference between ~$30 and ~$399/month.
4. **Stage H checklist item:** delete or replace every `price_bars` row with
   `source='yfinance'` before any non-owner user can log in. `YFinanceProvider`
   (`backend/app/providers/price/yfinance_provider.py`, Stage B1) tags every row it
   writes this way specifically so this cleanup is a single `WHERE source='yfinance'`
   away, not an audit.
5. **Stage H licence guard, done 2026-11-20 (interim, not a replacement for the
   purge above):** `OWNER_USER_IDS` (`app/auth.py::is_owner`, fail-closed --
   empty/unset means NO ONE, not even the real owner, sees it). Deliberately NOT
   the Supabase Admin API-based "how many real users exist" guard originally
   sketched for this item -- simpler, and keeps no service-role key in the
   backend at all. It buys time to run a pilot with more than one allowed user
   before the provider switch lands; it does not replace deleting the yfinance
   rows once a licensed provider is in place.

   **An independent review (2026-11-20) found the first version of this guard
   only covered one route** (`GET /instruments/{id}/bars`), while `GET
   /watchlist`'s `latest_close` and the entire `/strategy-runs/*` surface
   (signals, fills, metrics, CSV export -- all derivable from yfinance-sourced
   bars) leaked the same data to any allowlisted non-owner user with zero
   check. Fixed the same day, in three places:
   - `GET /instruments/{id}/bars`: 503 if any returned row is yfinance-sourced
     and the caller isn't the owner (unchanged from the original version).
   - `GET /watchlist`: `latest_close` is null and the new `latest_close_restricted`
     field is `true` for a yfinance-sourced instrument shown to a non-owner --
     a plain null would be indistinguishable from "no data at all", which is
     its own kind of silent omission.
   - `POST /strategy-runs` (`app/services/strategy_run.py::run_strategy`, new
     `caller_is_owner` parameter, **default `False` -- fail closed**): an
     instrument with any yfinance-sourced `price_bars` row is blocked for a
     non-owner caller with reason `"licence_restricted"`, in the SAME
     `blocked_instruments` dict the compliance gate already uses -- the
     instrument never enters the run at all, so nothing downstream (signals,
     fills, metrics, CSV) has to separately remember to re-check. `replay_strategy_run`
     needs no change: it already copies `blocked_instruments` from the original
     run verbatim rather than re-gating (same determinism reasoning as the
     compliance gate, see the Stage D/E design note below), so a licence block
     survives replay automatically.

---

## 4. Market data providers — research complete, decision pending

The strategy needs **5-minute OHLCV bars**. That granularity, not the symbol list, is
what makes this expensive.

Yahoo returns RY.TO 5-minute bars (verified live 2026-11-18, 234 bars, aligned with
AAPL), so TSX can be accumulated at $0 for development. Licensing is unchanged: the
~$399/month question applies only before non-owner users see prices.

### Key finding: TSX intraday is the cost wall

Every affordable provider follows the same pattern: **global coverage is end-of-day
only; 5-minute intraday is US-only.** TSX intraday data is controlled by TMX Group
and is not licensed to low-cost retail APIs.

| Provider | TSX 5-min | US 5-min | Free tier | Commercial from | Status |
|---|---|---|---|---|---|
| Marketstack | No | Yes (IEX) | 100 req/**month**, EOD, non-commercial | **$9.99/mo** | Best cheap commercial option |
| Polygon (Massive) | No | Yes | 5 calls/min, 2yr history | $2,499/mo | Good for dev, impossible for product |
| Twelve Data | EOD only | Yes | 8/min, 800/day | $29–79/mo | Has TSX but not TSX intraday |
| EODHD | Probably* | Yes | 20 calls/**day** | $399/mo | Only TSX hope; personal plan is €29.99 |
| Alpaca | No | Yes | IEX only | $99/mo | **Blocked** — see below |
| Finnhub | No | No | candles return 403 | $3,500/mo | Ruled out |
| FirstRate Data | No | Yes | sample only | one-off purchase | See below |

\* EODHD documents 5-minute bars for "Other Exchanges" from October 2020, which a
`.TO` ticker should fall under, but their docs do not confirm TSX explicitly.
**Verify with a free key before relying on it.**

### Provider notes that matter

- **Alpaca is doubly blocked.** Canada is not in their coverage, and Alpaca does not
  offer market data subscriptions to Canadian residents for regulatory reasons
  (they were targeting approval around Nov 2026). Do not build on it.
- **Finnhub's free tier cannot return candles at all** (403 on `/stock/candle`), and
  there is no middle tier — it jumps from free to $3,500/mo billed annually.
- **FirstRate Data is a one-off purchase, not a subscription**, and its licence
  permits derivative works and commercial use; it only forbids redistributing the
  raw data itself. Data is split- and dividend-adjusted. 5-min bars from Jan 2000.
  Individual tickers can be bought separately (we need ~20, not the full Russell
  3000 bundle). One month of updates included; $99.95/mo after that — so buy history
  once here, and get daily updates from a cheaper source.

### Free-source granularity limits (why "it's public" isn't enough)

Yahoo retention, which is the usual "it's free" answer:

| Interval | History available |
|---|---|
| Daily / weekly / monthly | Unlimited |
| 1 hour | 730 days |
| **5 minute** | **60 days** |
| 1 minute | 7 days |

The 60-day window **rolls**. Bars not captured today are gone permanently.

### 🔴 Highest-priority action

**Stand up the daily backfill job as early as possible and start accumulating our own
5-minute history.** Every day of delay loses a day of data that cannot be recovered
at any price. The same job works with any provider later — only the adapter changes.

### Architecture requirement that follows

Both market data and halal compliance go behind **adapter interfaces**, because the
provider decision is still open and will likely change:

```
PriceProvider (abstract)
├── MockProvider          # deterministic fixtures, used by tests
├── YFinanceProvider      # development only — NOT licensed for production
├── MarketstackProvider
└── EodhdProvider

ComplianceProvider (abstract)
├── ManualProvider        # MVP: admin form + CSV import
├── ZoyaProvider          # later, B2B API
└── MusaffaProvider       # later, B2B API
```

No provider-specific types, field names, or symbol formats may leak outside its
adapter.

---

## 5. Halal compliance — manual in MVP, by design

Zoya and Musaffa both have B2B APIs, but both are enterprise sales-gated (no
self-serve signup), so access takes weeks. **The MVP does not need them.**

The MVP implements an **admin compliance form + CSV import**, which is what the
developer plan's Stage C actually specifies, and matches the owner's intent that the
approved list be supplied to the app.

Each compliance record stores:
- symbol, status (`halal` / `not_halal` / `doubtful` / `not_covered`)
- source (`manual` / `zoya` / `musaffa`) and evidence (link or uploaded file)
- `checked_at` and `expires_at` (default 90 days)

**Hard rule: if a symbol's compliance record is missing, non-compliant, or expired,
the paper engine must refuse the order.** This is a must-pass test, not a warning.

The app never computes or infers a halal ruling itself. Ever.

**UNCONFIRMED ASSUMPTIONS (Stage C, flagged, not yet confirmed by the owner):**
- `doubtful` and `not_covered` block trading exactly like `not_halal`. Revisit if the
  owner wants `doubtful` to warn-but-allow instead.
- Default `expires_at` is `checked_at + 90 days`
  (`COMPLIANCE_DEFAULT_VALIDITY_DAYS`, `backend/app/config.py`).

**Gate implementation (Stage C):** `app/services/compliance.py`.
`effective_status(check, now)` returns `"ok"` or one of the five reason codes
(`no_record`, `not_halal`, `doubtful`, `not_covered`, `expired`); `assert_tradable
(session, instrument_id, now)` raises `ComplianceBlocked(reason)` and, on success,
returns the `compliance_checks.id` it used. `now` is always an explicit parameter,
never read from the wall clock inside the gate — see §7's determinism note for why.

`compliance_checks` is append-only, enforced by a DB trigger (migration 0003, on top
of the `uq_compliance_one_current` partial unique index from migration 0001): DELETE
always raises, and UPDATE raises unless `is_current` is the only column that changed
— the one flip the service layer performs (old current row → `false`, same
transaction as the new row's INSERT, in that order). Writes are admin-only
(`ADMIN_USER_IDS`, a comma-separated list of Supabase user UUIDs — **unset or empty
means NO ONE is admin**, fail closed, never "everyone"). Reads (`GET /watchlist`) are
open to any authenticated user.

### Demo environment fixtures — never a real ruling, never a real symbol

`scripts/seed_demo.py` (`app/demo_mode.py` enforces `APP_MODE=demo` can only ever
point at an isolated local database — see §12-adjacent README section "Demo
environment") seeds 6 **synthetic** instruments, never the 4 real ones, so that no
real company is ever labelled with any compliance status, even an obviously-fake
one:

| Symbol | Status | Notes |
|---|---|---|
| `DEMO-OK` | `halal`, valid | far from expiry |
| `DEMO-SOON` | `halal`, expires within 14 days | exercises the amber watchlist badge |
| `DEMO-EXPIRED` | `halal`, already expired | exercises the `expired` gate reason |
| `DEMO-NOTHALAL` | `not_halal` | |
| `DEMO-DOUBTFUL` | `doubtful` | |
| `DEMO-NOCOVER` | `not_covered` | |

The 4 real symbols (AAPL/MSFT/KO/RY) get **no** compliance record in the demo
database either — they show `no_record`, same as any newly-seeded symbol in
production. Every fixture's `notes` starts `"DEMO FIXTURE, NOT A RULING"`,
`evidence_url` is `https://example.invalid/demo-fixture`, and `created_by` is a
fixed nil UUID — unmistakable on inspection, backed up by the red "DEMO
ENVIRONMENT" banner the frontend shows on every screen (`GET /config`) whenever
`app_mode == "demo"`.

**Demo fixtures are never used for any real symbol outside `APP_MODE=demo`.** Real
compliance records enter production only through the admin form or the CSV import
(both above), after a human has verified the status against Zoya/Musaffa evidence
— never through this script, never inferred by the app itself.

---

## 6. Database schema — 8 tables

Names from the developer plan. All timestamps `TIMESTAMPTZ` stored in **UTC**. All
money and price columns `NUMERIC(18,4)` — never `float`/`double`.

1. **`instruments`** — symbol, exchange (MIC code), currency, name, active flag
2. **`compliance_checks`** — per §5 above; one current record per instrument plus history
3. **`price_bars`** — instrument_id, `ts` (bar open, UTC), interval, open, high, low,
   close, volume, source. Unique on `(instrument_id, ts, interval)` so re-imports are
   idempotent.
4. **`strategy_runs`** — the parameters of one backtest: instrument(s), date range,
   fast/slow MA periods, `reset_ma_each_session`, cost assumptions, `config_hash`,
   `started_at`, `status`. Stage D (migration 0004) added two more JSONB columns,
   both keyed by instrument_id: `blocked_instruments` (reason the gate rejected it)
   and `compliance_check_ids` (the `compliance_checks.id` the gate used for each
   compliant instrument — the snapshot the §7 determinism note depends on). Stage E
   (migration 0005) added `paper_status` (`'not_run'`/`'completed'`, default
   `'not_run'`) and `paper_completed_at` — the paper engine locks this row
   (`SELECT ... FOR UPDATE`), writes every order/fill, and flips the status in one
   transaction, so a second call (even on a run with zero orders) always sees
   `'completed'` and refuses rather than ever duplicating. `commission_per_order`,
   `spread_bps`, `slippage_bps` gained defaults (`1.00`, `2`, `1`) in the same
   migration — assumptions, not researched figures; §11 lists them as open.
5. **`signals`** — strategy_run_id, instrument_id, `signal_ts` (the bar that produced
   it), side (`buy`/`sell`), reason (human-readable), the MA values at that bar, and
   `execution_ts` (Stage D, migration 0004 — the next calendar-expected bar open,
   same trading session, never the next bar found in the data)
6. **`paper_orders`** — signal_id, instrument_id, side, qty, intended price,
   `execution_ts`, status, rejection reason. Migration 0005 replaced the old
   `qty > 0` CHECK with a status-conditional one (`filled` implies `qty > 0`;
   anything else allows `qty >= 0`, needed so a real zero-share rejection like
   `price_exceeds_max_position` can be recorded at all), and added a CHECK
   enumerating every `rejection_reason` value (kept in sync with
   `app/models/paper.py::REJECTION_REASONS`): `price_exceeds_max_position`,
   `insufficient_cash`, `position_already_open`, `no_open_position_to_sell`,
   `execution_bar_missing`, `execution_bar_missing_eod`,
   `instrument_frozen_unresolved_eod`.
7. **`paper_fills`** — order_id, fill price, qty, commission, spread cost, slippage
   cost, cash balance after. This table is the cash ledger's audit trail.
8. **`ai_reports`** — strategy_run_id, generated text, and the exact computed metrics
   the text was generated from (Stage G). Migration 0006 added `mode`,
   `requested_mode`, `llm_attempts`, `model_version`, `prompt_version`,
   `input_hash`, with `UNIQUE(strategy_run_id, requested_mode)` — see §7's Stage
   G subsection.

Sizing check: 20 symbols × 78 bars/day × 250 days ≈ 390k rows/year ≈ ~40 MB.
Supabase's free 500 MB is fine for several years.

---

## 7. Strategy rules — exact, and deterministic

**5/20 moving average crossover on 5-minute bars.**

- `MA5` = mean of the last 5 bars' close; `MA20` = mean of the last 20 bars' close
- MA5 crossing **above** MA20 → BUY signal
- MA5 crossing **below** MA20 → SELL signal

### No look-ahead — the rule most likely to be implemented wrong

A signal detected on the bar closing at time `T` must be executed at the **open of
the next bar**, `T+1`. The close of bar `T` is not knowable until bar `T` has ended,
so filling at bar `T`'s price means trading on future information.

This is a must-pass test (`no future bars`). Write it as a test before writing the
engine.

### Stage D: signal generation (`app/services/strategy.py`) — done 2026-11-18

`generate_signals` is a pure function: Decimal only (explicit `localcontext`, never
the ambient global context), no DB, no clock, no compliance gate, no bypass of any
kind. State = `sign(ma_fast - ma_slow)` ∈ {+1, 0, -1}; crossing the state compares
by **cross-multiplication** (`fast_sum*slow_period` vs `slow_sum*fast_period`), never
by dividing, so a near-tie can never flip from rounding error. `STRATEGY_RULES_VERSION
= "ma-cross-v1"` is one of the hashed config fields (below) — bump it whenever these
rules change, so an old run's hash is never mistaken for being comparable to a new
one.

**UNCONFIRMED ASSUMPTIONS (owner has not confirmed; they change results):**
1. MA continues across sessions by default. `reset_ma_each_session=True` instead
   requires the whole slow-period window to lie within one session (no signals in
   that session's first `slow_period` bars; the window never spans sessions).
2. EOD exit is modelled as happening at the OPEN of the session's last bar: a BUY
   signal whose execution bar would be that last bar is discarded outright (no time
   to hold before the forced exit); a SELL signal there is kept.
3. Gap-aware, and the tracked cross-state does **not** carry forward across a gap: a
   bar is evaluable only if every bar its slow-period window needs is present in the
   data. The first evaluable bar after any non-evaluable stretch only **sets** the
   state — it never emits a signal, even if comparing it to the state from before
   the gap would otherwise look like a cross. The pre-gap state is discarded, not
   reused.
4. Equal fast/slow MA is never a cross (state 0 satisfies neither side of a cross).

**SESSION BOUNDARY (a correctness fix, not a configurable assumption):**
`execution_ts` is always the next calendar-expected bar open in the **same trading
session** as the signal bar — never the next session's open, however many calendar
days away (a weekend or holiday included). A signal at a session's last bar is
discarded (both sides) rather than let it execute as an overnight position, since
this app is day-trading only. This is independent of `range_end`: a signal is never
discarded just because its execution bar falls outside the requested range or has
no data yet — that's Stage E's problem (refusing to fill), not this engine's, and
discarding on `range_end` would break prefix-stability (the no-look-ahead must-pass
test).

**Warm-up / lookback:** the run service (`app/services/strategy_run.py`) loads
`slow_period` calendar-expected bars *before* `range_start` so the first in-range
bar can still get a signal if a real cross lands there — those lookback bars feed
the state machine but can never themselves be emitted as a signal.

**`config_hash`** is `sha256` of canonical JSON (sorted keys, Decimals as strings,
instrument_ids sorted) over: `strategy_rules_version, instrument_ids, range_start,
range_end, fast_period, slow_period, reset_ma_each_session, interval, currency,
starting_cash, max_position_value, commission_per_order, spread_bps, slippage_bps`.
Money fields are quantized to `NUMERIC(18,4)` before hashing — a freshly-built
Decimal and the same value read back from the DB must hash identically, or a
replay's hash would differ from the original's for no reason but string formatting
(a real bug caught by the replay round-trip test; see `app/services/config_hash.py`).
Never hashed: `started_at`/`finished_at`/run id — bookkeeping, not config.

**Replay:** `POST /strategy-runs/{id}/replay` re-executes signal generation using
the ORIGINAL run's stored `compliance_check_ids`/`blocked_instruments` — it never
calls the gate again. See the Stage D/E design note below for why.

### Position and risk rules
- Max position size: **CAD $200** per symbol
- **Long-only** — no shorting, ever
- **One open position per symbol** at a time
- **All positions closed by session end** (day trading; nothing held overnight)
- No order placed if: price data is stale or missing, or halal verification is
  missing/expired
- Fills must include **spread, slippage, and commission**. A simulation without costs
  reports profits that cannot exist in reality.

### Determinism
Re-running the same `strategy_run` config over the same bars must produce byte-identical
signals, orders, and fills. No wall-clock reads, no unseeded randomness, no dict
ordering dependence inside the engine. This is a must-pass test.

### Stage E: paper engine (`app/services/paper_engine.py`) — done 2026-11-19

`simulate_fills` is pure, exactly like `generate_signals`: no DB, no clock, no
compliance gate (signals only ever exist for instruments already compliant when
the run started — Stage E never re-checks, and never imports the gate module at
all, a static-source-scan test proves it).

**Affordability** (a real bug caught before shipping): `qty*fill_price +
commission <= cash`. `spread_cost`/`slippage_cost` are reporting-only breakdowns
already folded into `fill_price` — an earlier draft added them to the cash check
a second time, which would have rejected affordable trades. Never add them to
the cash check or the cash movement.

**Sizing.** BUY: compute the adverse `fill_price` FIRST, then
`qty = floor(max_position_value / fill_price)` — the $200 cap can never be
exceeded by the bps adjustment, and `price_exceeds_max_position` is decided on
`fill_price`, not the raw open. SELL: always closes the ENTIRE open position,
never re-sized.

**Fills.** Adverse price adjustment: `adjustment_bps = spread_bps/2 +
slippage_bps`; BUY pays `raw_open*(1+adjustment_bps/10000)`, SELL receives
`raw_open*(1-adjustment_bps/10000)` — slippage always adverse, spread the usual
half-each-side convention. `fill_price` is rounded to 4dp FIRST
(`app/services/money.py::quantize_money`, the one named rounding rule); the cash
movement (`fill_price*qty +/- commission`) gets NO further rounding — exact,
since `qty` is a whole number and `commission` is already clean. A reconciliation
test recomputes every `cash_balance_after` from `paper_fills` rows alone
(`starting_cash` + movements) and requires byte-exact equality.

**EOD exit.** At every session's end, any instrument still holding a position
gets a synthetic sell at that session's last bar's open (reusing the calendar
logic Stage D already uses for the same bar). If that bar is missing, the order
is rejected (`execution_bar_missing_eod`) and the instrument is **frozen for the
rest of the run** — no further orders processed for it
(`instrument_frozen_unresolved_eod`) — rather than ever carrying an unresolved
position into a later session and selling it there. A regular (non-EOD)
signal-driven sell failing on a missing bar does NOT freeze the instrument; only
an EOD-exit failure does.

**Ordering — an ENGINEERING CONVENTION, not a business rule.** The global event
queue (every signal across every instrument, plus one EOD checkpoint per
instrument per session) is sorted by `(execution_ts, sells-and-EOD-checkpoints
before buys, instrument_id)`. A same-bar sell can free cash for a same-bar buy;
which instrument's buy "wins" when two buys compete for scarce cash is just
whichever has the lower `instrument_id` — no trading-priority meaning.

**Run state.** `strategy_runs.paper_status`/`paper_completed_at` (migration
0005). `run_paper_engine` locks the run row (`SELECT ... FOR UPDATE`), checks
`paper_status`, writes every order/fill, and sets the status in ONE transaction.
A second call — even against a run that produced zero orders — raises
`PaperEngineAlreadyRun` (409 at the route layer) rather than ever duplicating. A
real two-connection test (not a mock) proves the row lock actually serializes two
concurrent callers into exactly one set of rows.

**Ownership.** `GET /strategy-runs/{id}`, `/signals`, `/replay`,
`POST /strategy-runs/{id}/paper-run`, `GET .../orders`, `GET .../fills` all check
that `strategy_runs.user_id` equals the caller — not found and not-yours both
return 404, never a 403 that would leak existence. (The first three of these
were Stage D routes missing this check entirely until Stage E added it
everywhere at once — a real gap, not a hypothetical one.)

### Stage D/E design note: when the compliance gate runs in a backtest
The compliance gate (`app/services/compliance.py::assert_tradable`, built Stage C) is
evaluated **once, at run start, using the real clock** — not re-evaluated
point-in-time for every bar inside the historical simulation. The run then snapshots
which `compliance_checks.id` it used (the id `assert_tradable` returns on success) on
the `strategy_run`, so re-running an identical config stays byte-identical even if the
real compliance record expires or changes between the two runs.

**Why not point-in-time gating inside the simulation:** a historical backtest over,
say, the last 60 days would need a compliance record that was already current on day
1 of that window. In practice the first-ever compliance check for a symbol is
recorded on the day an admin adds it — so point-in-time gating would block every
trade before that day, for every backtest that includes it. That defeats backtesting
entirely for any newly-added symbol. Evaluating once at run start (real clock) avoids
this; the snapshot makes that one evaluation reproducible on rerun.

`assert_tradable(session, instrument_id, now)` already supports this: `now` is always
an explicit parameter (the real clock, passed in once by the Stage D/E run-setup
code, never read internally), and it returns the `compliance_checks.id` it used on
success specifically so the caller can record it.

Stage D implements both sides of this: `run_strategy(..., now)` (`app/services/
strategy_run.py`) is the one caller of `assert_tradable`, always with a `now` passed
in by the route (`datetime.now(timezone.utc)`) — never read from the wall clock
inside the service itself, so the service stays testable with a fixed clock.
`replay_strategy_run(session, run_id, now)` is the other half: it copies the
original run's `compliance_check_ids`/`blocked_instruments` straight across and
**never calls `assert_tradable` again** — `now` there is bookkeeping only
(`started_at`/`finished_at` on the new run row), not gating. A real compliance
record expiring between the original run and a later replay cannot change the
replay's outcome, by construction, not by coincidence.

### Stage F: metrics, Virtual Trades, CSV export — done 2026-11-19

`app/services/metrics.py::compute_metrics` is Decimal, computed server-side from
`paper_orders`/`paper_fills` only -- never `price_bars`, never a live price.

**Equity curve is REALIZED, not cash-ledger.** A cash-ledger curve (cash after
every fill, buys included) shows the cost of opening a position as an instant
"loss" before anything is actually lost -- buying 2 shares for ~$300 out of
$1000 would plot as a ~70% drawdown on day one. The curve instead starts at
`starting_cash` and adds one point per CLOSED round trip (a filled buy paired
with the filled sell that closed it), moved by that trade's own realized P&L --
never a point at the buy. `max_drawdown` is computed on this curve. Cost: it
does not reflect an open position's paper gains or losses while open, or at all
if the position is never closed -- stated as a caveat next to the chart, not
hidden.

**An unresolved position is excluded entirely**, not counted as a trade or a
loss: not in `trades_count`, not in `realized_pnl`, no point on the curve. Its
fees (already paid) still count in `total_fees` -- that money is genuinely
spent regardless of whether the position ever resolves. Unresolved positions
only ever arise from Stage E's EOD-exit freeze.

**Nullable metrics.** The API returns `null` (never the string `"no trades"`)
for `total_return`, `realized_pnl`, `win_rate`, `avg_win`, `avg_non_win` when
undefined -- `win_rate`/`avg_win`/`avg_non_win` are null independently (e.g.
one closed trade that lost gives a defined `win_rate` of `0.0000` but a null
`avg_win`, since there's nothing to average). `trades_count`, `total_fees`,
`max_drawdown`, `unresolved_positions` are always real numbers, never null --
each is well-defined even with zero trades. `avg_loss` is named `avg_non_win`:
its population is every closed trade with `pnl <= 0` (losses AND breakeven
trades) -- a breakeven trade is not a win, so it can't be excluded from both
buckets.

**CSV export** (`GET /strategy-runs/{id}/orders.csv`, `.../fills.csv`,
`app/services/csv_safety.py`): two distinct rules for two distinct kinds of
cell, never conflated. `csv_safe` (free text only -- instrument symbol/name,
the plain-language rejection message) prefixes `'` if the value starts with
`= + - @` or a tab/CR. `numeric_cell` (qty, prices, cash balances) instead
*asserts* the value matches `^-?\d+(\.\d+)?$` and passes it through raw --
applying `csv_safe` there too would prefix a legitimate negative number (`-`
is itself a trigger character), corrupting real P&L/cash figures. Both files
include a `run_id` column. The run's config and the paper-trading disclaimer
are never embedded in the CSV itself -- they're shown on the page, above the
download button.

**Plain-language rejection text** (`app/services/rejection_messages.py`) is one
shared source for the Virtual Trades table and the CSV export, so the two can
never say something different about the same code. `price_exceeds_max_position`
is the one reason that's parameterized -- it names both the actual price and
the configured cap, not just the generic reason.

**Strategy Lab's price warning**: `GET /watchlist` gained `latest_close`
(Decimal-as-string) so the picker can show, next to each instrument's gate
status, whether its last close already exceeds the currently-entered max
position value -- entirely a read of existing `price_bars` data, no new
computation.

**`GET /strategy-runs`** (own runs only, paginated) backs the Dashboard /
Virtual Trades run picker -- same ownership rule as every other `/strategy-runs`
route. **`GET /strategy-runs/{id}/metrics` returns 409**, not an empty/zero
result, when `paper_status != 'completed'` -- there is nothing to compute yet,
and a 409 says so instead of silently rendering a misleading "no trades".

No new migration -- every Stage F addition reads existing Stage D/E columns;
nothing new is stored.

### Stage G: AI-generated summaries — done 2026-11-20

Two modes, both producing the same `AiReport` shape: `template` (always available,
deterministic, no network call) and `llm` (sends only the already-computed metrics
to Anthropic's API; falls back to `template` on any failure or missing key, and the
fallback is always labeled `mode="template"`, never mislabeled as `"llm"`).

**Number safety is the core design constraint.** Neither the LLM nor the template
is ever allowed to write a digit or a number word into the text a user reads.
Both produce RAW text containing only whitelisted `{placeholder}` tokens (e.g.
`{trades_count}`, `{realized_pnl}`); `app/services/ai_summary_input.py`'s
`ALLOWED_PLACEHOLDERS` is the fixed whitelist, and trusted code substitutes the
real values only after the raw text has passed every guard. This means a model
can describe a run in prose but can never put its own number into what the user
reads -- the numbers always come from the same computed metrics Stage F already
produced, never from the model's own arithmetic.

**Guards** (`app/services/ai_summary_guards.py`), run on the RAW (pre-substitution)
text from both modes:
- No digit, no number word (`zero`..`twenty`, tens, `hundred`, `thousand`,
  `million`, ordinals) anywhere in the raw text.
- No placeholder outside `ALLOWED_PLACEHOLDERS`.
- No banned phrase -- word-boundary regex, not substring (so "learned" and
  "unpredictable" pass), covering `should`/`recommend`/`advise`/`likely`/
  `expected`/`forecast`/`opportunity`/`predict`/`profit`/`guarantee`/`halal`/
  `haram`/"you should buy|sell"/"will rise|fall|go up|go down", plus any URL.
- Text is normalized with Unicode NFKC and has zero-width characters
  (`​‌‍﻿⁠`) stripped BEFORE matching, so a homoglyph or a
  zero-width character inserted mid-word cannot slip a banned word past the regex.
- Length-capped (`MAX_GENERATED_PORTION_CHARS = 1500`) before any of the above.

The app never computes or infers a halal ruling itself (§5) -- the banned-phrase
list enforces that the summary can't either.

**Cost cap** (migration 0006): `ai_reports` gained `requested_mode`,
`llm_attempts` (every attempt -- success, failure, or guard rejection, all
count), `model_version`, `prompt_version`, `input_hash`, with
`UNIQUE(strategy_run_id, requested_mode)`. `generate_or_fetch_ai_summary` locks
the `strategy_runs` row (`SELECT ... FOR UPDATE`) before deciding whether to
call the LLM, so two simultaneous requests for the same run cause exactly one
LLM call, proven with a real two-thread/two-connection test, not a mock. A
repeat request may retry the LLM only while `llm_attempts < 3` on that row AND
the caller's per-user rolling-24h daily cap (`ai_summary_daily_cap`, default 5,
counted via `SUM(llm_attempts)`) is not hit; once either limit is hit, every
further request for that run silently gets the `template` fallback instead of
erroring.

**Heading by mode** (`HEADING_BY_MODE` in `app/services/ai_summary.py`):
template -> "Plain-language summary of a simulated result (automatic, no AI)";
llm -> "AI-generated explanation of a simulated result". The heading is always
picked from the report's ACTUAL `mode`, never its `requested_mode` -- a
fallback always shows the template heading, so a user can never be shown AI
-labeled text that wasn't actually AI-generated.

**Secret handling**: `ai_api_key` is a `SecretStr` (`app/config.py`) -- empty
means LLM mode is unavailable outright (fail-closed, same shape as
`ALLOW_DEV_DATA_SOURCE`). `app/services/ai_summary_llm.py` is the only code that
ever calls `.get_secret_value()`, and never logs or re-raises the raw value;
tested that `repr()`/`str()` of the `Settings` object, and of every exception
raised on the LLM call path, never contain the key.

**Rendering**: the frontend (`AiSummaryPanel.tsx`) renders `report_text` as
plain text only -- a React text child, never `dangerouslySetInnerHTML`, never a
markdown-to-HTML step. A render test proves a `<script>` string in `report_text`
produces zero `<script>` DOM elements and shows up as literal visible text.

**Disclosure, shown next to the mode selector** (and here, so this file and the
UI never drift apart): *"LLM mode sends the numbers above to an external
service. Template mode sends nothing."*

**Prompt version guard**: `PROMPT_VERSION = "ai-summary-v1"` (`app/services/
ai_summary_template.py`) is hashed into `config_hash`-style pinning for the
template text itself -- a test pins a hash of the rendered template output
against a fixed fixture, so changing the wording fails the test until
`PROMPT_VERSION` is bumped and the pinned hash is updated on purpose, mirroring
`STRATEGY_RULES_VERSION`'s role in §7's `config_hash`.

**Ownership**: `POST /strategy-runs/{id}/ai-summary` and
`GET /strategy-runs/{id}/ai-summary` both use the same ownership check as every
other `/strategy-runs/*` route (§7's Stage E note) -- not found and not-yours
both return 404.

**No point-in-time reporting on live price/compliance data**: the summary is
generated entirely from `paper_orders`/`paper_fills`/Stage F's computed
`metrics`, exactly like the metrics themselves (never `price_bars`, never a
live price, never a fresh compliance lookup) -- see Stage F's note above on
`compute_metrics`.

---

## 8. Time, calendars, and money

**Timezones.** Market hours are America/New_York and America/Toronto; the owner is in
Asia/Dhaka; providers return various formats. Store everything in **UTC**, convert
only at the display layer. Getting this wrong silently corrupts every backtest.

**Market calendars.** Use `pandas_market_calendars` — a library, not an API: offline,
no key, no cost. Verified available calendars include `TSX`, `TSXV`, `XTSE`, `NYSE`,
`NASDAQ` (296 total).

It handles half-days correctly, which hardcoded holiday lists do not. Verified
example — TSX, December 2026 (times UTC):

```
2026-12-23  open 14:30  close 21:00   normal day
2026-12-24  open 14:30  close 18:00   half day, closes 3h early
2026-12-25  — absent —                Christmas
2026-12-28  — absent —                Boxing Day observed
2026-12-29  open 14:30  close 21:00   normal day
```

Never hardcode market hours or holidays. Query the calendar.

**Money.** `Decimal` in Python, `NUMERIC(18,4)` in Postgres. `float` arithmetic
(`0.1 + 0.2 = 0.30000000000000004`) will fail the ledger tests.

---

## 9. Must-pass tests

From the developer plan. Write these as real pytest tests, not manual checks.

1. **Math and loss calculations are correct** — including losses, not just gains —
   done (Stage E: a hand-computed realized LOSS, not just a gain)
2. **Buys exceeding available cash are rejected** — done (Stage E: `insufficient_cash`)
3. **Non-compliant or expired-compliance symbols are rejected** — done (Stage C gate)
4. **No future bars are used** (next-bar execution enforced) — done (Stage D:
   prefix-stability + perturbation tests, `tests/test_strategy_engine.py`)
5. **No fills when the market is closed or on a holiday** — done (Stage E:
   `execution_bar_missing`/`execution_bar_missing_eod`, never an invented price)
6. **API failure produces an error, never an invented or interpolated price** — done (Stage B)
7. **Re-running an identical strategy run produces identical results** — done
   (Stage D proved it for signals; Stage E's `simulate_fills` is pure and proves
   the same for orders and fills)
8. **Single-currency simulation: mixed-currency run is rejected** — done (Stage D;
   not in the original developer plan's numbered list, added when it turned out to
   be a one-line validation Stage D already needed to do)

Test 6 deserves emphasis: on provider failure, fail loudly. Never forward-fill,
interpolate, or fall back to the last known price to keep a run alive.

---

## 10. Build stages and status

| Stage | Scope | Status |
|---|---|---|
| A | Repo, Docker Compose, migrations, login, README | Done (live-verified 2026-11-18) |
| B | Provider adapter, backfill, data validation, charts | Done 2026-11-18 (B1 backend pipeline + B2 instruments/bars API and chart UI) |
| C | Compliance form, evidence, expiry, order blocking | Done 2026-11-18 (gate + admin-only writes + CSV import + watchlist UI) |
| D | 5/20 MA strategy, next-bar execution, deterministic | Done 2026-11-18 (signal generation only; no cash/sizing/fills/orders -- that's Stage E) |
| E | Paper engine: cash ledger, positions, fills, EOD exit, costs | Done 2026-11-19 (sizing, fills, EOD exit + freeze, cash ledger, ownership checks retrofitted onto Stage D too) |
| F | Dashboard: watchlist, results, trade history, CSV export | Done 2026-11-19 (Strategy Lab, Virtual Trades, Dashboard, realized equity curve, CSV export, no new migration) |
| G | AI review — plain-language summaries from computed metrics only | Done 2026-11-20 (number-safety placeholder substitution, phrase guard, cost cap with row-lock + daily cap, SecretStr key, plain-text-only rendering, migration 0006 on db_test and production) |
| H | Automated tests, security checks, demo data, licence check | Demo data done 2026-11-18 (ahead of schedule). Security hardening done 2026-11-20: user allowlist, interim yfinance licence guard, JWT issuer validation + security test suite, per-user rate limiting, error-response safety test, gitleaks/pip-audit/npm audit run, read-only production structural checks, `docs/RELEASE_CHECKLIST.md` written. **Still open:** commit/push (blocks clean-clone test + independent review), licensed provider + yfinance purge, GitHub Actions cron, backup role + restore drill not yet executed against production — see `docs/RELEASE_CHECKLIST.md` |

Target milestones: wk 1 requirements + provider decision · wk 2 watchlist +
compliance · wk 3–4 backtest engine, ledger, dashboard · wk 5 testing + AI
summaries · wk 6 owner pilot (virtual funds only).

### Four screens
1. **Watchlist** — symbol, halal status, last checked, data timestamp
2. **Strategy Lab** — pick instrument and date range, run backtest
3. **Virtual Trades** — every simulated buy/sell with reason, price, fees; CSV export
4. **Dashboard** — starting cash, ending equity, P/L, win rate, avg win/loss, max drawdown

---

## 11. Open decisions — need the owner, not a developer

1. **US-only MVP, or include TSX?** US-only costs $0 in development and $9.99/month
   live. Including TSX means ~$399/month. All of the owner's own examples were US
   symbols. **This is the decision that gates the provider choice.**
2. **Does the owner pilot count as personal or commercial use?** Ask the chosen
   provider in writing. Worth ~$370/month.
3. **Live suggestions, or backtest first?** The developer plan specifies a backtest
   ("Strategy Lab" with a date range). The owner's description also describes live
   intraday monitoring that says "sell now". These share the same foundation (data,
   compliance, ledger, dashboard) but differ in the top layer.
   **Recommended approach:** build the engine so one code path runs over both
   historical bars (backtest) and live bars (suggestions), satisfying both without a
   rewrite.
4. **Stage E's default cost assumptions.** `commission_per_order=1.00`,
   `spread_bps=2`, `slippage_bps=1` (migration 0005) are placeholders, not
   researched figures for the actual US/TSX symbols in play. Stage E does not
   block on these being confirmed, but real backtest results depend on them.

---

## 12. Known gotchas

- **Supabase free projects pause after ~7 days of inactivity.** The daily cron keeps
  the project awake; if the cron is disabled, the project sleeps.
- **Render's free tier sleeps after 15 minutes idle** and takes ~60s to wake, so an
  in-container cron cannot be relied on. The external GitHub Actions cron hits an
  endpoint, which also wakes the container.
- **EODHD intraday bars are not split/dividend adjusted** and are finalised ~2–3
  hours after market close. Plan the backfill job to run after that window.
- **EODHD request windows** are capped per call: 120 days for 1m, 600 days for 5m.
- **Stage G constraint:** the LLM may only put computed metrics into plain language.
  It must never predict prices, never recommend trades, and never produce a halal
  ruling. Pass it numbers the code already calculated — never raw bars to "analyse".
- Every screen needs the disclaimer: *paper trading only, not financial advice,
  halal status from a third party and not our own ruling.*
- **Stage G disclosure**, shown next to the AI summary's mode selector: *"LLM
  mode sends the numbers above to an external service. Template mode sends
  nothing."* Keep this in sync with `frontend/src/components/AiSummaryPanel.tsx`
  and README.md if the wording changes.
- **Stage H: `ALLOWED_USER_IDS` and `OWNER_USER_IDS` (`app/config.py`) are
  fail-closed, same shape as `ADMIN_USER_IDS`** -- empty/unset means NO ONE,
  never "everyone"/"no restriction". `app/auth.py::current_user` enforces
  `ALLOWED_USER_IDS` (admins count as allowed automatically) for every
  authenticated route; `is_owner` gates yfinance-sourced bars separately (see
  §3 point 5) -- being allowed to use the app implies nothing about being
  licensed to see yfinance prices, and vice versa. Disabling public sign-ups in
  the Supabase dashboard (README) is still required -- `ALLOWED_USER_IDS` alone
  only stops an unauthorized user from calling the API, not from obtaining a
  valid JWT in the first place.
- **Stage H: JWT verification now validates `iss`, not just `aud`/`exp`/the
  signature.** `app/auth.py::_expected_issuer` derives the expected value from
  `SUPABASE_URL` (`"<url>/auth/v1"`); skipped only if `SUPABASE_URL` itself is
  unset. `alg=none` and a tampered signature were already rejected by PyJWT's
  own `algorithms=` allowlist and signature check -- Stage H added tests
  proving it (`backend/tests/test_jwt_security.py`), not new code for those two.
- **Stage H: rate limiting (`app/rate_limit.py`) is single-instance only** --
  in-process memory, no Redis. Running more than one backend instance would
  give each instance its own independent counter (see README's "Stage H: rate
  limiting is single-instance only" for the full explanation). Fine today
  (Render free tier, one container); revisit if that changes.
- **Never run a command that expands environment variables or `.env` contents into
  its output** — `docker compose config`, `env`, `printenv`, `cat .env` (or any
  editor/pager on it), `set` with no args, and the like. `docker compose config`
  specifically re-renders the WHOLE compose file, including services you didn't
  ask about, interpolating `.env` as it goes — asking it about `db_test`/`db_demo`
  still prints the `backend` service's real `DATABASE_URL` password, `JOB_SECRET`,
  and Supabase keys in plaintext (this happened once, 2026-11-19: the output was
  caught and the temp file it was written to was deleted immediately, but it had
  already landed in the tool-output transcript — the DB password and `JOB_SECRET`
  were rotated afterward). For container/port checks, use `docker inspect` or
  `docker port`, which report the running container's actual state and never touch
  `.env`.
- **Always connect to `db_test`/`db_demo` with the literal `127.0.0.1`, never the
  hostname `"localhost"`.** Docker Desktop on this stack only publishes those
  ports on IPv4 — a `"[::1]:PORT:PORT"` binding in `docker-compose.yml` is
  silently ignored (confirmed via `docker inspect`, 2026-11-19), and this
  machine resolves `"localhost"` to the IPv6 loopback first, so every connection
  stalls on that failed attempt before falling back to IPv4 (one migration
  command took over two minutes instead of being instant). `app/config.py`'s
  `test_database_url` default and every doc/script example already use
  `127.0.0.1` for this reason.
