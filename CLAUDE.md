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
   fast/slow MA periods, cost assumptions, a seed/config hash, started_at, status
5. **`signals`** — strategy_run_id, instrument_id, `signal_ts` (the bar that produced
   it), side (`buy`/`sell`), reason (human-readable), and the MA values at that bar
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

1. **Math and loss calculations are correct** — including losses, not just gains
2. **Buys exceeding available cash are rejected**
3. **Non-compliant or expired-compliance symbols are rejected**
4. **No future bars are used** (next-bar execution enforced)
5. **No fills when the market is closed or on a holiday**
6. **API failure produces an error, never an invented or interpolated price**
7. **Re-running an identical strategy run produces identical results**

Test 6 deserves emphasis: on provider failure, fail loudly. Never forward-fill,
interpolate, or fall back to the last known price to keep a run alive.

---

## 10. Build stages and status

| Stage | Scope | Status |
|---|---|---|
| A | Repo, Docker Compose, migrations, login, README | Done (live-verified 2026-11-18) |
| B | Provider adapter, backfill, data validation, charts | Done 2026-11-18 (B1 backend pipeline + B2 instruments/bars API and chart UI) |
| C | Compliance form, evidence, expiry, order blocking | Not started |
| D | 5/20 MA strategy, next-bar execution, deterministic | Not started |
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
