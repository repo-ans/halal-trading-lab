"""compliance_checks append-only trigger

The partial unique index enforcing one current row per instrument
(uq_compliance_one_current) already exists from migration 0001 -- this migration
does NOT duplicate it. It adds the actual append-only guarantee instead: DELETE is
always rejected, and UPDATE is rejected unless the only column that changed is
is_current. That is the one flip the service layer performs (the old current row's
is_current -> false, in the same transaction as the new row's INSERT); nothing else
may ever modify a compliance_checks row after it's written.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_FUNCTION = """
CREATE OR REPLACE FUNCTION compliance_checks_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'compliance_checks is append-only: DELETE is not permitted (id=%)', OLD.id;
    END IF;

    -- TG_OP = 'UPDATE': every column except is_current must be unchanged.
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.instrument_id IS DISTINCT FROM OLD.instrument_id
       OR NEW.status IS DISTINCT FROM OLD.status
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.evidence_url IS DISTINCT FROM OLD.evidence_url
       OR NEW.evidence_file_path IS DISTINCT FROM OLD.evidence_file_path
       OR NEW.notes IS DISTINCT FROM OLD.notes
       OR NEW.checked_at IS DISTINCT FROM OLD.checked_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION
            'compliance_checks is append-only: only is_current may be updated (id=%)',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER trg_compliance_checks_append_only
BEFORE UPDATE OR DELETE ON compliance_checks
FOR EACH ROW EXECUTE FUNCTION compliance_checks_append_only();
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_compliance_checks_append_only ON compliance_checks")
    op.execute("DROP FUNCTION IF EXISTS compliance_checks_append_only()")
