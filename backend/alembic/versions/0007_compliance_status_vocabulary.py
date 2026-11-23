"""Rename compliance_checks.status vocabulary to Zoya's own enum names.

halal -> COMPLIANT, not_halal -> NON_COMPLIANT, doubtful -> QUESTIONABLE,
not_covered -> UNRATED. Decided 2026-11-23, after the owner obtained Zoya
sandbox access (CLAUDE.md section 5's Zoya-lookup subsection): this is a pure
vocabulary rename, not a behavior change -- the four states, the blocking
rules, and "a human still verifies every record" are all unchanged.

This is a DATA migration, not just a schema one: compliance_checks is
append-only (migration 0003's trigger rejects any UPDATE that touches
`status`), so the bulk rewrite below has to run with that trigger lifted for
this one scripted statement, then restore it immediately after -- the
append-only guarantee is never left open beyond this single migration.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-23
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_OLD_TO_NEW = {
    "halal": "COMPLIANT",
    "not_halal": "NON_COMPLIANT",
    "doubtful": "QUESTIONABLE",
    "not_covered": "UNRATED",
}
_NEW_VALUES_SQL = "'COMPLIANT','NON_COMPLIANT','QUESTIONABLE','UNRATED'"
_OLD_VALUES_SQL = "'halal','not_halal','doubtful','not_covered'"

_RECREATE_TRIGGER = """
CREATE TRIGGER trg_compliance_checks_append_only
BEFORE UPDATE OR DELETE ON compliance_checks
FOR EACH ROW EXECUTE FUNCTION compliance_checks_append_only();
"""
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS trg_compliance_checks_append_only ON compliance_checks"


def upgrade() -> None:
    # Lift the append-only trigger (migration 0003) for this one data fix --
    # the function itself (compliance_checks_append_only()) is untouched and
    # stays defined; only the trigger invoking it is dropped and recreated.
    op.execute(_DROP_TRIGGER)

    op.drop_constraint("ck_compliance_status", "compliance_checks", type_="check")
    case_sql = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in _OLD_TO_NEW.items())
    op.execute(f"UPDATE compliance_checks SET status = CASE status {case_sql} END")
    op.create_check_constraint(
        "ck_compliance_status", "compliance_checks", f"status IN ({_NEW_VALUES_SQL})")

    op.execute(_RECREATE_TRIGGER)


def downgrade() -> None:
    op.execute(_DROP_TRIGGER)

    op.drop_constraint("ck_compliance_status", "compliance_checks", type_="check")
    case_sql = " ".join(f"WHEN '{new}' THEN '{old}'" for old, new in _OLD_TO_NEW.items())
    op.execute(f"UPDATE compliance_checks SET status = CASE status {case_sql} END")
    op.create_check_constraint(
        "ck_compliance_status", "compliance_checks", f"status IN ({_OLD_VALUES_SQL})")

    op.execute(_RECREATE_TRIGGER)
