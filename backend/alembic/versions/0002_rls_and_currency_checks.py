"""enable RLS on all tables (deny-by-default) + currency format checks

Supabase exposes the public schema through PostgREST using the anon key, which ships
in the frontend bundle. With RLS off, anyone could write to these tables directly and
bypass every engine rule. RLS is enabled with NO policies, so anon/authenticated roles
get nothing. The backend connects directly as a role that bypasses RLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("instruments", "compliance_checks", "price_bars", "strategy_runs",
          "signals", "paper_orders", "paper_fills", "ai_reports")


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.create_check_constraint("ck_instruments_currency_fmt", "instruments",
                               "currency ~ '^[A-Z]{3}$'")
    op.create_check_constraint("ck_strategy_runs_currency_fmt", "strategy_runs",
                               "currency ~ '^[A-Z]{3}$'")


def downgrade() -> None:
    op.drop_constraint("ck_strategy_runs_currency_fmt", "strategy_runs")
    op.drop_constraint("ck_instruments_currency_fmt", "instruments")
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
