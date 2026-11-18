"""Read-only preview of what the signal engine would compute for one symbol over
one or more consecutive trading sessions. Development/debugging only -- never
writes anything, never creates a strategy_runs or signals row, never calls the
compliance gate.

The ma_fast/ma_slow columns come straight from the engine's own
compute_ma_series (app/services/strategy.py) -- the exact same calendar schedule,
gap-awareness, and reset_ma_each_session rule generate_signals uses to decide a
cross, just reported per-bar instead of gated into a state machine. An earlier
version of this script computed its own simplified rolling mean instead, which
silently ignored reset_ma_each_session (both modes rendered identically) -- never
duplicate the engine's windowing here again.

Usage:
    python scripts/preview_signals.py --symbol AAPL --exchange XNAS --date 2026-11-17
    python scripts/preview_signals.py --symbol AAPL --exchange XNAS \\
        --date 2026-11-16 --end-date 2026-11-17 \\
        --fast 5 --slow 20 --csv out.csv
"""
import argparse
import csv as csv_module
import sys
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on sys.path

from sqlalchemy import select, text  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.market_calendar import EXCHANGE_CALENDARS, session_bounds  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar  # noqa: E402
from app.services.strategy import compute_ma_series, generate_signals  # noqa: E402
from app.services.strategy_run import _load_bars  # noqa: E402 -- read-only, reused as-is

BANNER = "DEV PREVIEW, NOT A TRADING SIGNAL, writes nothing, not gated"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", required=True, help="MIC code, e.g. XNAS")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, the first session to preview")
    parser.add_argument("--end-date", default=None,
                        help="YYYY-MM-DD, the last session to preview (inclusive). "
                             "Defaults to --date (a single session).")
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--reset-ma-each-session", action="store_true")
    parser.add_argument("--csv", type=Path, default=None, help="also write a CSV to this path")
    args = parser.parse_args()

    print(BANNER)

    calendar = EXCHANGE_CALENDARS.get(args.exchange)
    if calendar is None:
        sys.exit(f"no market calendar configured for exchange {args.exchange!r}")

    start_day = date_cls.fromisoformat(args.date)
    end_day = date_cls.fromisoformat(args.end_date) if args.end_date else start_day
    if end_day < start_day:
        sys.exit("--end-date must not be before --date")

    start_bounds = session_bounds(start_day, calendar)
    if start_bounds is None:
        sys.exit(f"{args.date} is not a trading session on {calendar}")
    end_bounds = session_bounds(end_day, calendar)
    if end_bounds is None:
        sys.exit(f"{args.end_date} is not a trading session on {calendar}")
    range_start, range_end = start_bounds[0], end_bounds[1]

    with session_scope() as session:
        # Read-only at the database level, not just by convention -- this
        # connection physically cannot INSERT/UPDATE/DELETE for the rest of the
        # transaction, regardless of what runs below. Must be the first statement.
        session.execute(text("SET TRANSACTION READ ONLY"))

        instrument = session.scalar(
            select(Instrument).where(Instrument.symbol == args.symbol,
                                     Instrument.exchange == args.exchange)
        )
        if instrument is None:
            sys.exit(f"no instrument {args.symbol}/{args.exchange} found")

        # _load_bars already includes slow_period's worth of lookback before
        # range_start -- the same bars generate_signals and compute_ma_series both
        # need, fetched once and shared between the two calls below.
        engine_bars = _load_bars(session, instrument.id, calendar, range_start, range_end, args.slow)
        signals = generate_signals(
            engine_bars, fast_period=args.fast, slow_period=args.slow, calendar=calendar,
            range_start=range_start, range_end=range_end,
            reset_ma_each_session=args.reset_ma_each_session,
        )
        signal_by_ts = {s.signal_ts: s for s in signals}
        mas = compute_ma_series(
            engine_bars, fast_period=args.fast, slow_period=args.slow, calendar=calendar,
            range_start=range_start, range_end=range_end,
            reset_ma_each_session=args.reset_ma_each_session,
        )

        rows = session.scalars(
            select(PriceBar).where(
                PriceBar.instrument_id == instrument.id,
                PriceBar.interval == SUPPORTED_INTERVAL,
                PriceBar.ts >= range_start,
                PriceBar.ts < range_end,
            ).order_by(PriceBar.ts)
        ).all()

    table_rows = []
    for r in rows:
        ma_fast, ma_slow = mas.get(r.ts, (None, None))
        sig = signal_by_ts.get(r.ts)
        table_rows.append({
            "ts": r.ts.isoformat(), "open": str(r.open), "high": str(r.high),
            "low": str(r.low), "close": str(r.close),
            "ma_fast": "" if ma_fast is None else str(ma_fast),
            "ma_slow": "" if ma_slow is None else str(ma_slow),
            "signal": "" if sig is None else sig.side,
            "execution_ts": "" if sig is None else sig.execution_ts.isoformat(),
        })

    header = ["ts", "close", "ma_fast", "ma_slow", "signal"]
    print("  ".join(f"{h:>20}" for h in header))
    for row in table_rows:
        print("  ".join(f"{row[h]:>20}" for h in header))

    if args.csv is not None:
        csv_header = ["ts", "open", "high", "low", "close", "ma_fast", "ma_slow", "signal",
                      "execution_ts"]
        with open(args.csv, "w", newline="") as f:
            writer = csv_module.DictWriter(f, fieldnames=csv_header)
            writer.writeheader()
            writer.writerows(table_rows)
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
