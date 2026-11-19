"""Stage E: paper-trading run state and order-rejection constraints.

strategy_runs.paper_status / paper_completed_at: the paper engine
(app/services/paper_engine.py) locks this row (SELECT ... FOR UPDATE), checks
paper_status, writes every order/fill, and flips the status in ONE transaction --
a second call (even for a run that produced zero orders) sees 'completed' and
refuses rather than ever duplicating rows.

paper_orders.qty: was `qty > 0` unconditionally, which can never represent a
real, recorded rejection with qty=0 (e.g. price_exceeds_max_position). Replaced
with a status-conditional rule: filled implies qty > 0; anything else allows
qty >= 0.

paper_orders.rejection_reason: previously only presence/absence was constrained
(must be non-null iff status='rejected'); this adds the actual enumerated set of
reasons, kept in sync with app/models/paper.py::REJECTION_REASONS.

commission_per_order / spread_bps / slippage_bps: had no default at all. These
are assumptions, not researched figures (CLAUDE.md section 11, open CEO decision)
-- Stage E does not block on confirming them, but a run created without
specifying them needs SOME value.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_runs", sa.Column(
        "paper_status", sa.Text(), nullable=False, server_default="not_run"))
    op.add_column("strategy_runs", sa.Column(
        "paper_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_paper_status", "strategy_runs", "paper_status IN ('not_run','completed')")

    op.alter_column("strategy_runs", "commission_per_order", server_default="1.00")
    op.alter_column("strategy_runs", "spread_bps", server_default="2")
    op.alter_column("strategy_runs", "slippage_bps", server_default="1")

    op.drop_constraint("ck_order_qty_positive", "paper_orders", type_="check")
    op.create_check_constraint(
        "ck_order_qty_by_status", "paper_orders",
        "(status = 'filled' AND qty > 0) OR (status <> 'filled' AND qty >= 0)")
    op.create_check_constraint(
        "ck_order_rejection_reason_enum", "paper_orders",
        "rejection_reason IS NULL OR rejection_reason IN ("
        "'price_exceeds_max_position','insufficient_cash','position_already_open',"
        "'no_open_position_to_sell','execution_bar_missing','execution_bar_missing_eod',"
        "'instrument_frozen_unresolved_eod')")


def downgrade() -> None:
    op.drop_constraint("ck_order_rejection_reason_enum", "paper_orders", type_="check")
    op.drop_constraint("ck_order_qty_by_status", "paper_orders", type_="check")
    op.create_check_constraint("ck_order_qty_positive", "paper_orders", "qty > 0")

    op.alter_column("strategy_runs", "commission_per_order", server_default=None)
    op.alter_column("strategy_runs", "spread_bps", server_default=None)
    op.alter_column("strategy_runs", "slippage_bps", server_default=None)

    op.drop_constraint("ck_paper_status", "strategy_runs", type_="check")
    op.drop_column("strategy_runs", "paper_completed_at")
    op.drop_column("strategy_runs", "paper_status")
