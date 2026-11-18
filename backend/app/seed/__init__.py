"""Instruments come from this config file, never hardcoded in Python (CLAUDE.md
section 4 architecture requirement). Seeding ONLY touches the instruments table --
it must never create a compliance_checks row, so every seeded symbol stays blocked
from trading by default until a human reviews and approves it (CLAUDE.md section 5)."""
import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.instrument import Instrument

DEFAULT_SEED_PATH = Path(__file__).with_name("instruments.json")


def seed_instruments(session: Session, path: Path = DEFAULT_SEED_PATH) -> int:
    """Idempotent upsert keyed on (symbol, exchange) -- the same unique constraint the
    instruments table already enforces. Returns the number of rows touched."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        return 0

    stmt = pg_insert(Instrument).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_instruments_symbol_exchange",
        set_={"currency": stmt.excluded.currency, "name": stmt.excluded.name,
              "active": True},
    )
    session.execute(stmt)
    session.commit()
    return len(rows)
