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
   compliant instrument — the snapshot the §7 determinism note depends on).
5. **`signals`** — strategy_run_id, instrument_id, `signal_ts` (the bar that produced
   it), side (`buy`/`sell`), reason (human-readable), the MA values at that bar, and
   `execution_ts` (Stage D, migration 0004 — the next calendar-expected bar open,
   same trading session, never the next bar found in the data)
6. **`paper_orders`** — signal_id, instrument_id, side, qty, intended price,
   `execution_ts`, status, rejection reason
7. **`paper_fills`** — order_id, fill price, qty, commission, spread cost, slippage
   cost, cash balance after. This table is the cash ledger's audit trail.
8. **`ai_reports`** — strategy_run_id, generated text, and the exact computed metrics
   the text was generated from (Stage G)

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

1. **Math and loss calculations are correct** — including losses, not just gains (Stage E)
2. **Buys exceeding available cash are rejected** (Stage E)
3. **Non-compliant or expired-compliance symbols are rejected** — done (Stage C gate)
4. **No future bars are used** (next-bar execution enforced) — done (Stage D:
   prefix-stability + perturbation tests, `tests/test_strategy_engine.py`)
5. **No fills when the market is closed or on a holiday** (Stage E)
6. **API failure produces an error, never an invented or interpolated price** — done (Stage B)
7. **Re-running an identical strategy run produces identical results** — Stage D
   proved this for signals (determinism + replay tests); stays open until Stage E
   extends the same guarantee to orders and fills
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
| E | Paper engine: cash ledger, positions, fills, EOD exit, costs | Not started |
| F | Dashboard: watchlist, results, trade history, CSV export | Not started |
| G | AI review — plain-language summaries from computed metrics only | Not started |
| H | Automated tests, security checks, demo data, licence check | Not started |

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
