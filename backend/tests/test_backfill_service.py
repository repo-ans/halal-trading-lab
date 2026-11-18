"""DB-writing tests: run against the local throwaway test database only (db_conn,
RUN_DB_TESTS=1 -- see conftest.py and CLAUDE.md section 2). The provider is a fixture
(no network) -- these tests verify the insert/idempotency logic, not yfinance."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa

from app.market_calendar import session_bounds
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from app.providers.price.base import Bar, PriceProvider, ProviderError
from app.services.backfill import run_backfill
from app.services.validation import expected_bar_count
from tests.conftest import run_db_tests

NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)  # Monday, after the bar closes
BAR_TS = datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)


class FakeProvider(PriceProvider):
    name = "fake"

    def __init__(self, bars_by_symbol: dict[str, list[Bar] | Exception]):
        self._bars = bars_by_symbol

    def get_bars(self, symbol, interval, start, end):
        result = self._bars.get(symbol, [])
        if isinstance(result, Exception):
            raise result
        return result


def _make_instrument(db_conn, symbol="ZFAKE", exchange="XNAS", currency="USD") -> Instrument:
    instrument = Instrument(symbol=symbol, exchange=exchange, currency=currency,
                            name="fixture instrument")
    db_conn.add(instrument)
    db_conn.flush()
    return instrument


def _bar(ts=BAR_TS, open="100", high="101", low="99", close="100.5", volume=1000) -> Bar:
    return Bar(ts=ts, open=Decimal(open), high=Decimal(high), low=Decimal(low),
              close=Decimal(close), volume=volume)


@run_db_tests
def test_inserts_valid_bars(db_conn):
    instrument = _make_instrument(db_conn)
    provider = FakeProvider({"ZFAKE": [_bar()]})

    report = run_backfill(db_conn, provider, [instrument], NOW)

    ir = report.instruments[0]
    assert ir.fetched == 1
    assert ir.inserted == 1
    assert ir.rejected == []
    assert ir.error is None

    stored = db_conn.scalars(
        sa.select(PriceBar).where(PriceBar.instrument_id == instrument.id)
    ).all()
    assert len(stored) == 1
    assert stored[0].close == Decimal("100.5")


@run_db_tests
def test_rerun_is_idempotent(db_conn):
    instrument = _make_instrument(db_conn)
    provider = FakeProvider({"ZFAKE": [_bar()]})

    run_backfill(db_conn, provider, [instrument], NOW)
    second = run_backfill(db_conn, provider, [instrument], NOW)

    assert second.instruments[0].inserted == 0
    assert second.instruments[0].duplicate == 1
    count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(PriceBar)
        .where(PriceBar.instrument_id == instrument.id)
    )
    assert count == 1


@run_db_tests
def test_differing_refetch_is_logged_and_stored_value_kept(db_conn):
    instrument = _make_instrument(db_conn)
    first = FakeProvider({"ZFAKE": [_bar(close="100.5")]})
    run_backfill(db_conn, first, [instrument], NOW)

    changed = FakeProvider({"ZFAKE": [_bar(high="200", close="150.25")]})
    report = run_backfill(db_conn, changed, [instrument], NOW)

    ir = report.instruments[0]
    assert ir.inserted == 0
    assert len(ir.differs_kept_stored) == 1

    stored = db_conn.scalars(
        sa.select(PriceBar).where(PriceBar.instrument_id == instrument.id)
    ).all()
    assert len(stored) == 1
    assert stored[0].close == Decimal("100.5")  # original value, never overwritten


@run_db_tests
def test_invalid_bar_is_rejected_and_logged_not_inserted(db_conn):
    instrument = _make_instrument(db_conn)
    bad = _bar(high="1", low="99")  # high < low
    provider = FakeProvider({"ZFAKE": [bad]})

    report = run_backfill(db_conn, provider, [instrument], NOW)

    ir = report.instruments[0]
    assert ir.inserted == 0
    assert len(ir.rejected) == 1
    count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(PriceBar)
        .where(PriceBar.instrument_id == instrument.id)
    )
    assert count == 0


@run_db_tests
def test_forming_bar_is_dropped(db_conn):
    instrument = _make_instrument(db_conn)
    forming = _bar(ts=NOW.replace(minute=58))  # opened 2 min before "now" -- not closed
    provider = FakeProvider({"ZFAKE": [forming]})

    report = run_backfill(db_conn, provider, [instrument], NOW)

    ir = report.instruments[0]
    assert ir.dropped_forming == 1
    assert ir.inserted == 0


@run_db_tests
def test_provider_error_isolated_to_one_instrument(db_conn):
    ok = _make_instrument(db_conn, symbol="ZOK")
    broken = _make_instrument(db_conn, symbol="ZBROKEN")
    provider = FakeProvider({
        "ZOK": [_bar()],
        "ZBROKEN": ProviderError("simulated upstream failure"),
    })

    report = run_backfill(db_conn, provider, [ok, broken], NOW)

    by_symbol = {ir.symbol: ir for ir in report.instruments}
    assert by_symbol["ZOK"].inserted == 1
    assert by_symbol["ZOK"].error is None
    assert by_symbol["ZBROKEN"].inserted == 0
    assert by_symbol["ZBROKEN"].error == "simulated upstream failure"


@run_db_tests
def test_large_close_jump_is_flagged_but_still_inserted(db_conn):
    instrument = _make_instrument(db_conn)
    first = FakeProvider({"ZFAKE": [_bar(close="100.5")]})
    run_backfill(db_conn, first, [instrument], NOW)

    # next 5-minute bar, close jumps 40% -- not necessarily wrong, just notable
    jumped = _bar(ts=BAR_TS.replace(minute=35), open="100.5", high="145", low="100",
                 close="140.5")
    second = FakeProvider({"ZFAKE": [jumped]})
    report = run_backfill(db_conn, second, [instrument], NOW)

    ir = report.instruments[0]
    assert ir.inserted == 1  # still inserted -- a warning, not a rejection
    assert len(ir.large_jumps) == 1
    assert "140.5" in ir.large_jumps[0]


@run_db_tests
def test_small_close_move_is_not_flagged(db_conn):
    instrument = _make_instrument(db_conn)
    first = FakeProvider({"ZFAKE": [_bar(close="100.5")]})
    run_backfill(db_conn, first, [instrument], NOW)

    small_move = _bar(ts=BAR_TS.replace(minute=35), open="100.5", high="106", low="100",
                      close="105.3")  # ~4.8% move
    second = FakeProvider({"ZFAKE": [small_move]})
    report = run_backfill(db_conn, second, [instrument], NOW)

    assert report.instruments[0].large_jumps == []


@run_db_tests
def test_gap_report_reflects_missing_bars(db_conn):
    instrument = _make_instrument(db_conn)
    provider = FakeProvider({"ZFAKE": [_bar()]})  # only 1 of 78 expected bars that day

    report = run_backfill(db_conn, provider, [instrument], NOW)

    ir = report.instruments[0]
    day_gap = next(g for g in ir.gaps if g.day == BAR_TS.date())
    assert day_gap.expected == 78
    assert day_gap.actual == 1


@run_db_tests
def test_recent_session_gap_pct_reflects_most_recent_completed_session(db_conn):
    instrument = _make_instrument(db_conn)
    provider = FakeProvider({"ZFAKE": [_bar()]})  # 1 of 78 expected bars that day

    # NOW (15:00 UTC) is before BAR_TS's day closes (21:00), so that day isn't
    # "completed" yet at NOW -- use a time well past its close instead.
    later = BAR_TS.replace(hour=22)
    report = run_backfill(db_conn, provider, [instrument], later)

    ir = report.instruments[0]
    expected_pct = Decimal(78 - 1) / Decimal(78) * 100  # 77 of 78 bars missing
    assert ir.recent_session_gap_pct == expected_pct


@run_db_tests
def test_suspected_close_indexed_day_flows_through_run_backfill(db_conn):
    """End-to-end: FIX 2 -- a full day of close-indexed bars (ts shifted +5min from
    the real bar open, so the first expected bar at session_open is missing but one
    lands exactly at session_close) must surface as InstrumentReport.
    suspected_close_indexed_days, not just pass as a normal (if slightly late) day."""
    instrument = _make_instrument(db_conn)
    day = date(2026, 11, 13)  # Friday, fully completed relative to NOW (Monday 15:00)
    day_open, day_close = session_bounds(day, "NASDAQ")
    n = expected_bar_count(day_open, day_close)
    close_indexed_bars = [
        _bar(ts=day_open + timedelta(minutes=5 * (i + 1)), open="100", high="101",
            low="99", close="100.5")
        for i in range(n)
    ]
    provider = FakeProvider({"ZFAKE": close_indexed_bars})

    report = run_backfill(db_conn, provider, [instrument], NOW)

    ir = report.instruments[0]
    assert day.isoformat() in ir.suspected_close_indexed_days


@run_db_tests
def test_open_indexed_day_not_suspected(db_conn):
    instrument = _make_instrument(db_conn)
    day = date(2026, 11, 13)
    day_open, day_close = session_bounds(day, "NASDAQ")
    n = expected_bar_count(day_open, day_close)
    open_indexed_bars = [
        _bar(ts=day_open + timedelta(minutes=5 * i), open="100", high="101",
            low="99", close="100.5")
        for i in range(n)
    ]
    provider = FakeProvider({"ZFAKE": open_indexed_bars})

    report = run_backfill(db_conn, provider, [instrument], NOW)

    assert report.instruments[0].suspected_close_indexed_days == []
