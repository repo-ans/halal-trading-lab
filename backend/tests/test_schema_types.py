"""Schema guardrails: no float/double, money is NUMERIC(18,4), timestamps are TIMESTAMPTZ.
Checks both the ORM models and the SQL the migration actually emits (offline, no DB)."""
import io
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.models import Base

EXPECTED_TABLES = {"instruments", "compliance_checks", "price_bars", "strategy_runs",
                   "signals", "paper_orders", "paper_fills", "ai_reports"}


def test_eight_tables():
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
    assert "FLOAT" not in sql and "DOUBLE" not in sql and "REAL" not in sql
    for t in EXPECTED_TABLES:
        assert f"CREATE TABLE {t.upper()}" in sql
        assert f"ALTER TABLE {t.upper()} ENABLE ROW LEVEL SECURITY" in sql
    assert sql.count("CREATE POLICY") == 0  # deny-by-default: no policies
    assert sql.count("CURRENCY ~ '^[A-Z]{3}$'") == 2


@pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1",
                    reason="live DB test: set RUN_DB_TESTS=1 with a real DATABASE_URL")
def test_rls_enabled_on_all_tables_live():
    from app.config import get_settings
    eng = sa.create_engine(get_settings().database_url)
    with eng.connect() as c:
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
