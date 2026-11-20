"""Stage G: ai_reports gains mode tracking and a per-run cost-cap counter.

ai_reports was scaffolded in Stage A (strategy_run_id, report_text, metrics,
model, generated_at) but never used until now. This adds:

mode / requested_mode: `mode` is what was actually rendered (template or llm --
drives which heading is shown, and a fallback is NEVER labelled 'llm' even if
that's what was asked for). `requested_mode` is what the caller asked for, and
is the unique key: a row is per (strategy_run_id, requested_mode), not per
(strategy_run_id, mode), so a request for 'llm' that fell back to the template
can still be retried later without colliding with an independently-requested
'template' row for the same run.

llm_attempts: every LLM attempt (success, failure, or guard rejection) for this
row increments this, capped at 3 by the service layer -- not re-attempted
forever once a run has repeatedly failed.

model_version / prompt_version / input_hash: provenance. prompt_version is
bumped whenever the template/prompt wording changes (see
app/services/ai_summary_template.py's pinned-output test); input_hash is a
sha256 of the exact (minimal, numbers-only) input the report was generated
from.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ai_reports has never been written to (Stage A scaffold, unused through
    # Stage F) -- NOT NULL with no existing rows to backfill, same reasoning as
    # migration 0004's signals.execution_ts.
    op.add_column("ai_reports", sa.Column("mode", sa.Text(), nullable=False))
    op.add_column("ai_reports", sa.Column("requested_mode", sa.Text(), nullable=False))
    op.add_column("ai_reports", sa.Column(
        "llm_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ai_reports", sa.Column("model_version", sa.Text(), nullable=True))
    op.add_column("ai_reports", sa.Column("prompt_version", sa.Text(), nullable=False))
    op.add_column("ai_reports", sa.Column("input_hash", sa.Text(), nullable=False))

    op.create_check_constraint("ck_ai_report_mode", "ai_reports", "mode IN ('template','llm')")
    op.create_check_constraint(
        "ck_ai_report_requested_mode", "ai_reports", "requested_mode IN ('template','llm')")
    op.create_check_constraint(
        "ck_ai_report_llm_attempts_nonneg", "ai_reports", "llm_attempts >= 0")
    op.create_unique_constraint(
        "uq_ai_report_run_requested_mode", "ai_reports", ["strategy_run_id", "requested_mode"])


def downgrade() -> None:
    op.drop_constraint("uq_ai_report_run_requested_mode", "ai_reports", type_="unique")
    op.drop_constraint("ck_ai_report_llm_attempts_nonneg", "ai_reports", type_="check")
    op.drop_constraint("ck_ai_report_requested_mode", "ai_reports", type_="check")
    op.drop_constraint("ck_ai_report_mode", "ai_reports", type_="check")

    op.drop_column("ai_reports", "input_hash")
    op.drop_column("ai_reports", "prompt_version")
    op.drop_column("ai_reports", "model_version")
    op.drop_column("ai_reports", "llm_attempts")
    op.drop_column("ai_reports", "requested_mode")
    op.drop_column("ai_reports", "mode")
