"""Seeds the isolated demo database: migrates it, seeds the real instruments
(no compliance record -- they show "no_record"), creates 6 synthetic
"DEMO-*" instruments with fixture compliance records covering every gate
branch, and copies their price bars from production over a READ-ONLY
connection. Never invents, infers, or derives a compliance status from any real
screening data or from knowledge of any real company (CLAUDE.md section 5) --
every fixture status below is an arbitrary label on a synthetic instrument,
never on AAPL/MSFT/KO/RY themselves.

Requires APP_MODE=demo and DATABASE_URL pointing at the demo database (the
SAME rule app/demo_mode.py enforces for the API server -- this script calls the
identical function, so there is exactly one place either rule can drift).

The production (source) connection comes from SOURCE_DATABASE_URL in the
environment, or an interactive hidden prompt if unset -- never a CLI argument,
so it never ends up in shell history. It is opened read-only (SET TRANSACTION
READ ONLY) and only ever SELECTed from.

Usage (PowerShell):
    $env:APP_MODE = "demo"
    $env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5434/halal_demo"
    $env:SOURCE_DATABASE_URL = "<production DATABASE_URL>"   # or omit to be prompted
    python scripts/seed_demo.py
"""
import getpass
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on sys.path

import sqlalchemy as sa  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import read_only_session, session_scope  # noqa: E402
from app.demo_mode import assert_demo_db_is_safe  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar  # noqa: E402
from app.seed import seed_instruments  # noqa: E402
from app.services.compliance import create_compliance_check, validate_new_check  # noqa: E402

DEMO_EVIDENCE_URL = "https://example.invalid/demo-fixture"
DEMO_NOTES = ("DEMO FIXTURE, NOT A RULING -- status assigned arbitrarily for demo "
             "purposes only, never derived from any real screening or company.")
DEMO_CREATED_BY = UUID("00000000-0000-0000-0000-000000000000")

# Mirrors app/seed/instruments.json -- kept local (not imported) so this demo-only
# script never needs the production seed file's shape to stay compatible with it.
REAL_SYMBOL_INFO = {
    "AAPL": {"exchange": "XNAS", "currency": "USD"},
    "MSFT": {"exchange": "XNAS", "currency": "USD"},
    "KO": {"exchange": "XNYS", "currency": "USD"},
    "RY": {"exchange": "XTSE", "currency": "CAD"},
}


def build_fixture_specs(now: datetime) -> dict[str, dict]:
    """One spec per synthetic instrument. `now` is a parameter, never read
    internally, so this stays testable with a fixed clock (same principle as
    the compliance gate and the strategy engine)."""
    return {
        "DEMO-OK": {
            "name": "Demo Fixture OK (not a real company)", "source_symbol": "AAPL",
            "status": "COMPLIANT", "checked_at": now - timedelta(days=1),
            "expires_at": now + timedelta(days=90),
        },
        "DEMO-SOON": {
            "name": "Demo Fixture Soon-Expiring (not a real company)", "source_symbol": "MSFT",
            "status": "COMPLIANT", "checked_at": now - timedelta(days=80),
            "expires_at": now + timedelta(days=10),  # within the 14-day amber threshold
        },
        "DEMO-EXPIRED": {
            "name": "Demo Fixture Expired (not a real company)", "source_symbol": "KO",
            "status": "COMPLIANT", "checked_at": now - timedelta(days=100),
            "expires_at": now - timedelta(days=10),
        },
        "DEMO-NOTHALAL": {
            "name": "Demo Fixture Not-Halal (not a real company)", "source_symbol": "RY",
            "status": "NON_COMPLIANT", "checked_at": now - timedelta(days=1),
            "expires_at": now + timedelta(days=90),
        },
        "DEMO-DOUBTFUL": {
            "name": "Demo Fixture Doubtful (not a real company)", "source_symbol": "AAPL",
            "status": "QUESTIONABLE", "checked_at": now - timedelta(days=1),
            "expires_at": now + timedelta(days=90),
        },
        "DEMO-NOCOVER": {
            "name": "Demo Fixture Not-Covered (not a real company)", "source_symbol": "MSFT",
            "status": "UNRATED", "checked_at": now - timedelta(days=1),
            "expires_at": now + timedelta(days=90),
        },
    }


def _resolve_source_url() -> str:
    url = os.environ.get("SOURCE_DATABASE_URL")
    if url:
        return url
    return getpass.getpass("Source (production) DATABASE_URL: ")


def _refuse_if_same_database(source_url: str, target_url: str) -> None:
    s, t = sa.engine.make_url(source_url), sa.engine.make_url(target_url)
    if (s.host, s.port, s.database) == (t.host, t.port, t.database):
        sys.exit("refusing to seed: SOURCE_DATABASE_URL and DATABASE_URL point at the "
                "same host/port/database")


def _refuse_if_target_has_non_demo_data(session) -> None:
    count = session.scalar(sa.text(
        "SELECT COUNT(*) FROM compliance_checks "
        "WHERE notes IS NULL OR notes NOT LIKE 'DEMO FIXTURE, NOT A RULING%'"
    ))
    if count:
        sys.exit(f"refusing to seed: the target database already holds {count} "
                "non-demo compliance_checks row(s) -- this doesn't look like a fresh "
                "demo database")


def _migrate_target(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def _upsert_synthetic_instruments(session, specs: dict[str, dict]) -> dict[str, int]:
    rows = []
    for symbol, spec in specs.items():
        source_info = REAL_SYMBOL_INFO[spec["source_symbol"]]
        rows.append({
            "symbol": symbol, "exchange": source_info["exchange"],
            "currency": source_info["currency"], "name": spec["name"],
        })
    stmt = pg_insert(Instrument).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_instruments_symbol_exchange",
        set_={"currency": stmt.excluded.currency, "name": stmt.excluded.name, "active": True},
    )
    session.execute(stmt)
    session.flush()

    ids = {}
    for symbol, spec in specs.items():
        inst = session.scalar(select(Instrument).where(
            Instrument.symbol == symbol,
            Instrument.exchange == REAL_SYMBOL_INFO[spec["source_symbol"]]["exchange"],
        ))
        ids[symbol] = inst.id
    return ids


def _copy_bars_for_symbol(source_session, target_session, source_symbol: str,
                          target_instrument_ids: list[int]) -> int:
    info = REAL_SYMBOL_INFO[source_symbol]
    source_inst = source_session.scalar(select(Instrument).where(
        Instrument.symbol == source_symbol, Instrument.exchange == info["exchange"]))
    if source_inst is None:
        print(f"warning: {source_symbol}/{info['exchange']} not found in the source database")
        return 0

    rows = source_session.scalars(select(PriceBar).where(
        PriceBar.instrument_id == source_inst.id, PriceBar.interval == SUPPORTED_INTERVAL,
    ).order_by(PriceBar.ts)).all()
    if not rows:
        print(f"warning: no production bars found for {source_symbol}")
        return 0

    total = 0
    for target_id in target_instrument_ids:
        payload = [
            {"instrument_id": target_id, "ts": r.ts, "interval": r.interval, "open": r.open,
             "high": r.high, "low": r.low, "close": r.close, "volume": r.volume,
             "source": r.source}  # original source tag kept, never overwritten
            for r in rows
        ]
        stmt = pg_insert(PriceBar).values(payload).on_conflict_do_nothing(
            constraint="uq_price_bars_idempotent")
        target_session.execute(stmt)
        total += len(payload)
    return total


def _create_fixture(session, symbol: str, instrument_id: int, spec: dict, now: datetime) -> None:
    error = validate_new_check(
        status=spec["status"], checked_at=spec["checked_at"], expires_at=spec["expires_at"],
        evidence_url=DEMO_EVIDENCE_URL, evidence_file_path=None, now=now,
    )
    if error is not None:
        sys.exit(f"refusing to seed {symbol}: the real validation rejected this fixture "
                f"({error}) -- stopping instead of inserting directly")
    create_compliance_check(
        session, instrument_id=instrument_id, status=spec["status"], source="manual",
        checked_at=spec["checked_at"], expires_at=spec["expires_at"], created_by=DEMO_CREATED_BY,
        evidence_url=DEMO_EVIDENCE_URL, notes=DEMO_NOTES,
    )


def main() -> None:
    settings = get_settings()
    assert_demo_db_is_safe(settings)  # same function main.py uses -- refuses if not demo+local

    source_url = _resolve_source_url()
    _refuse_if_same_database(source_url, settings.database_url)

    print("Running migrations on the demo database...")
    _migrate_target(settings.database_url)

    with session_scope() as target:
        _refuse_if_target_has_non_demo_data(target)

        print("Seeding real instruments (no compliance record)...")
        seed_instruments(target)  # commits internally

        now = datetime.now(timezone.utc)
        specs = build_fixture_specs(now)

        print("Creating synthetic DEMO-* instruments...")
        synthetic_ids = _upsert_synthetic_instruments(target, specs)

        print("Copying price bars from production (read-only)...")
        targets_by_source: dict[str, list[int]] = {}
        for symbol, spec in specs.items():
            targets_by_source.setdefault(spec["source_symbol"], []).append(synthetic_ids[symbol])
        for real_symbol in REAL_SYMBOL_INFO:
            real_inst = target.scalar(select(Instrument).where(
                Instrument.symbol == real_symbol,
                Instrument.exchange == REAL_SYMBOL_INFO[real_symbol]["exchange"]))
            all_targets = [real_inst.id] + targets_by_source.get(real_symbol, [])
            with read_only_session(source_url) as source:
                count = _copy_bars_for_symbol(source, target, real_symbol, all_targets)
            print(f"  {real_symbol}: {count} bars copied into {len(all_targets)} instrument(s)")

        print("Creating compliance fixtures...")
        for symbol, spec in specs.items():
            _create_fixture(target, symbol, synthetic_ids[symbol], spec, now)

        target.commit()

    print("Done.")


if __name__ == "__main__":
    main()
