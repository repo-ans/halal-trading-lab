"""Read-only preview of what the signal engine would compute for one symbol and
one trading session. Development/debugging only -- never writes anything, never
creates a strategy_runs or signals row, never calls the compliance gate.

The ma_fast/ma_slow columns printed here are a simple, non-gap-aware rolling mean
over whatever bars happen to be loaded, for eyeballing only -- they are NOT the
authoritative engine's windows (app/services/strategy.py), which are calendar-gap-
aware and compared by cross-multiplication, never by dividing. Only the "signal"
column reflects the real engine's output.

Usage:
    python scripts/preview_signals.py --symbol AAPL --exchange XNAS --date 2026-11-17
    python scripts/preview_signals.py --symbol AAPL --exchange XNAS --date 2026-11-17 \\
        --fast 5 --slow 20 --csv out.csv
"""
import argparse
import csv as csv_module
import sys
from datetime import date as date_cls
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on sys.path

from sqlalchemy import select, text  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.market_calendar import EXCHANGE_CALENDARS, session_bounds  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar  # noqa: E402
from app.services.strategy import Bar, generate_signals  # noqa: E402
from app.services.strategy_run import _load_bars  # noqa: E402  -- read-only, reused as-is

BANNER = "DEV PREVIEW, NOT A TRADING SIGNAL, writes nothing, not gated"
_QUANT = Decimal("0.0001")


def _display_mas(rows: list[PriceBar], fast_period: int, slow_period: int) -> dict:
    """ts -> (ma_fast|None, ma_slow|None). Plain trailing rolling mean over `rows`
    in the order given -- display only, see module docstring."""
    closes = [r.close for r in rows]
    out = {}
    for i, r in enumerate(rows):
        ma_fast = None
        ma_slow = None
        if i + 1 >= fast_period:
            window = closes[i + 1 - fast_period:i + 1]
            ma_fast = (sum(window) / Decimal(fast_period)).quantize(_QUANT, rounding=ROUND_HALF_UP)
        if i + 1 >= slow_period:
            window = closes[i + 1 - slow_period:i + 1]
            ma_slow = (sum(window) / Decimal(slow_period)).quantize(_QUANT, rounding=ROUND_HALF_UP)
        out[r.ts] = (ma_fast, ma_slow)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", required=True, help="MIC code, e.g. XNAS")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, the session to preview")
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--reset-ma-each-session", action="store_true")
    parser.add_argument("--csv", type=Path, default=None, help="also write a CSV to this path")
    args = parser.parse_args()

    print(BANNER)

    calendar = EXCHANGE_CALENDARS.get(args.exchange)
    if calendar is None:
        sys.exit(f"no market calendar configured for exchange {args.exchange!r}")

    day = date_cls.fromisoformat(args.date)
    bounds = session_bounds(day, calendar)
    if bounds is None:
        sys.exit(f"{args.date} is not a trading session on {calendar}")
    range_start, range_end = bounds

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

        engine_bars = _load_bars(session, instrument.id, calendar, range_start, range_end, args.slow)
        signals = generate_signals(
            engine_bars, fast_period=args.fast, slow_period=args.slow, calendar=calendar,
            range_start=range_start, range_end=range_end,
            reset_ma_each_session=args.reset_ma_each_session,
        )
        signal_by_ts = {s.signal_ts: s for s in signals}
        execution_by_ts = {s.signal_ts: s.execution_ts for s in signals}

        all_rows = session.scalars(
            select(PriceBar).where(
                PriceBar.instrument_id == instrument.id,
                PriceBar.interval == SUPPORTED_INTERVAL,
                PriceBar.ts < range_end,
            ).order_by(PriceBar.ts)
        ).all()
        mas = _display_mas(all_rows, args.fast, args.slow)
        rows = [r for r in all_rows if range_start <= r.ts < range_end]

    table_rows = []
    for r in rows:
        ma_fast, ma_slow = mas[r.ts]
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
