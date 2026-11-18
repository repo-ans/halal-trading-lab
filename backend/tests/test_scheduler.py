"""DB-writing: real inserts against the local test database (RUN_DB_TESTS=1)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from app.scheduler import _recently_backfilled
from tests.conftest import run_db_tests

NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)


def _make_instrument(db_conn, symbol="ZSCHED") -> Instrument:
    instrument = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    db_conn.add(instrument)
    db_conn.flush()
    return instrument


def _make_bar(instrument_id: int, ingested_at: datetime, source: str) -> PriceBar:
    return PriceBar(instrument_id=instrument_id, ts=ingested_at, interval="5m",
                    open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                    close=Decimal("100.5"), volume=1000, source=source,
                    ingested_at=ingested_at)


@run_db_tests
def test_recently_backfilled_true_within_window(db_conn):
    instrument = _make_instrument(db_conn)
    db_conn.add(_make_bar(instrument.id, NOW - timedelta(hours=1), "yfinance"))
    db_conn.commit()

    assert _recently_backfilled(db_conn, "yfinance", NOW) is True


@run_db_tests
def test_recently_backfilled_false_when_stale(db_conn):
    instrument = _make_instrument(db_conn)
    db_conn.add(_make_bar(instrument.id, NOW - timedelta(hours=10), "yfinance"))
    db_conn.commit()

    assert _recently_backfilled(db_conn, "yfinance", NOW) is False


@run_db_tests
def test_recently_backfilled_false_when_absent(db_conn):
    assert _recently_backfilled(db_conn, "yfinance", NOW) is False


@run_db_tests
def test_recently_backfilled_ignores_other_sources(db_conn):
    instrument = _make_instrument(db_conn)
    db_conn.add(_make_bar(instrument.id, NOW - timedelta(hours=1), "mock"))
    db_conn.commit()

    assert _recently_backfilled(db_conn, "yfinance", NOW) is False
