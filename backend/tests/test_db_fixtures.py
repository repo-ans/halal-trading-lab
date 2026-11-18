"""Proves the local test-database infrastructure (conftest.py: live_db_url,
_migrated_test_db, db_conn, transactional_session) actually works against a real
Postgres, not just that it imports. Runs the real migrations and real inserts against
the throwaway db_test service (docker-compose.yml, profile "test") -- never against
DATABASE_URL."""
import uuid

import sqlalchemy as sa

from tests.conftest import run_db_tests, transactional_session


@run_db_tests
def test_db_conn_fixture_basic_insert_and_select(db_conn):
    db_conn.execute(sa.text(
        "INSERT INTO instruments (symbol, exchange, currency, name) "
        "VALUES ('ZZZTEST2', 'XNAS', 'USD', 'fixture smoke test')"))
    count = db_conn.execute(sa.text(
        "SELECT count(*) FROM instruments WHERE symbol = 'ZZZTEST2'")).scalar()
    assert count == 1


@run_db_tests
def test_db_conn_regression_rolls_back_after_fixture_exits(_migrated_test_db):
    """Regression test for the rollback guarantee itself, not just a usage example.
    Deliberately drives transactional_session() directly (the exact code db_conn
    wraps) rather than the db_conn fixture, and never issues a cleanup DELETE -- the
    only thing that can make the row disappear is the mechanism under test. The check
    after the `with` block exits uses a brand-new engine/connection, entirely outside
    transactional_session's machinery, so it can't be fooled by, say, a within-
    transaction read that merely hasn't rolled back yet.

    Self-contained and order-independent (earlier drafts split this across two tests
    relying on pytest's default execution order, which silently passed if the "check"
    test was ever run alone). If transactional_session regresses to a raw Connection
    without join_transaction_mode="create_savepoint" -- the exact bug an earlier draft
    of this fixture had, which really leaked a row into db_test -- commit() below
    would commit for real and this test would fail."""
    unique_symbol = f"ZTEST_{uuid.uuid4().hex[:12]}"

    with transactional_session(_migrated_test_db) as session:
        session.execute(sa.text(
            "INSERT INTO instruments (symbol, exchange, currency, name) "
            "VALUES (:symbol, 'XNAS', 'USD', 'rollback regression test')"),
            {"symbol": unique_symbol})
        session.commit()  # only closes the inner SAVEPOINT, if the mechanism is sound
        mid_transaction_count = session.execute(sa.text(
            "SELECT count(*) FROM instruments WHERE symbol = :symbol"),
            {"symbol": unique_symbol}).scalar()
        assert mid_transaction_count == 1  # visible to itself while still "open"
    # transactional_session has now exited -- its finally block ran outer_tx.rollback()

    fresh_engine = sa.create_engine(_migrated_test_db)
    with fresh_engine.connect() as fresh_conn:
        count_after_exit = fresh_conn.execute(sa.text(
            "SELECT count(*) FROM instruments WHERE symbol = :symbol"),
            {"symbol": unique_symbol}).scalar()
    fresh_engine.dispose()

    assert count_after_exit == 0, (
        "a row inserted inside transactional_session survived its exit -- the "
        "SAVEPOINT rollback guarantee is broken")
