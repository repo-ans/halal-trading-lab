"""Schema guardrails: no float/double, money is NUMERIC(18,4), timestamps are TIMESTAMPTZ.
Checks both the ORM models and the SQL the migration actually emits (offline, no DB)."""
import io
import os
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.models import Base

# Grew to 10 with Replay Trading (migration 0008, 2026-11-23) -- CLAUDE.md
# section 6 updated to match; the name EXPECTED_TABLES kept as-is rather than
# renumbered, since it's just a set of names, not a claim about the count.
EXPECTED_TABLES = {"instruments", "compliance_checks", "price_bars", "strategy_runs",
                   "signals", "paper_orders", "paper_fills", "ai_reports",
                   "replay_sessions", "replay_fills"}


def test_ten_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_model_column_types():
    for table in Base.metadata.tables.values():
        for col in table.columns:
            t = col.type
            assert not isinstance(t, sa.Float), f"{table.name}.{col.name} is float/double"
            if isinstance(t, sa.Numeric):
                assert (t.precision, t.scale) == (18, 4), f"{table.name}.{col.name}"
            if isinstance(t, sa.DateTime):
                assert t.timezone, f"{table.name}.{col.name} is not TIMESTAMPTZ"


def test_migration_sql():
    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "head", sql=True)
    sql = cfg.output_buffer.getvalue().upper()
    assert "NUMERIC(18, 4)" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "TIMESTAMP WITHOUT TIME ZONE" not in sql
    # Word-boundary, not plain substring -- replay_fills.realized_pnl's column
    # name contains "REAL" as a substring (REALized) with no float type involved.
    assert not re.search(r"\bFLOAT\b", sql)
    assert not re.search(r"\bDOUBLE\b", sql)
    assert not re.search(r"\bREAL\b", sql)
    for t in EXPECTED_TABLES:
        assert f"CREATE TABLE {t.upper()}" in sql
        assert f"ALTER TABLE {t.upper()} ENABLE ROW LEVEL SECURITY" in sql
    assert sql.count("CREATE POLICY") == 0  # deny-by-default: no policies
    assert sql.count("CURRENCY ~ '^[A-Z]{3}$'") == 2


@pytest.mark.skipif(os.environ.get("RUN_LIVE_RLS_CHECK") != "1",
                    reason="live, read-only check against the production Supabase "
                           "project: set RUN_LIVE_RLS_CHECK=1")
def test_rls_enabled_on_all_tables_live_readonly():
    """Checks the REAL production database, not the local test database -- this is
    the one test allowed to touch DATABASE_URL, and it must stay strictly read-only.
    Gated behind its own marker (RUN_LIVE_RLS_CHECK), separate from RUN_DB_TESTS,
    which governs the DB-WRITING tests against the local throwaway database (see
    conftest.py). `SET TRANSACTION READ ONLY` makes Postgres itself reject any write
    inside this transaction; the connection is never committed, only closed, so even a
    read-only transaction's implicit state is discarded, not persisted."""
    from app.config import get_settings

    eng = sa.create_engine(get_settings().database_url)
    with eng.connect() as c:
        c.execute(sa.text("SET TRANSACTION READ ONLY"))
        rows = c.execute(sa.text(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY(:t)"),
            {"t": sorted(EXPECTED_TABLES)}).all()
        policies = c.execute(sa.text(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")).scalar()
    assert {r[0] for r in rows} == EXPECTED_TABLES
    assert all(r[1] for r in rows), rows
    assert policies == 0
