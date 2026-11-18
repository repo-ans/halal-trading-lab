import os
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa

# Needed so app.config / alembic env import without a real database, for tests that
# never touch a DB at all. Must NOT fire when RUN_DB_TESTS=1: os.environ wins over
# the .env file in pydantic-settings, so setting this unconditionally would silently
# replace a real DATABASE_URL from .env with this dummy and point live tests at
# localhost instead of the configured project.
if os.environ.get("RUN_DB_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:pw@localhost:5432/test")

run_db_tests = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="DB-writing test: set RUN_DB_TESTS=1 (uses the local throwaway test "
           "database -- see docker-compose.yml's db_test, profile 'test')",
)


@pytest.fixture(scope="session")
def live_db_url() -> str:
    """The ONLY database DB-writing tests may touch: a disposable local Postgres,
    never the production Supabase project. Refuses (hard failure, not a skip) if
    TEST_DATABASE_URL is missing, equal to DATABASE_URL, or not on localhost -- those
    are the only things standing between a test TRUNCATE and production data."""
    from app.config import get_settings

    s = get_settings()
    if not s.test_database_url:
        pytest.fail("RUN_DB_TESTS=1 but TEST_DATABASE_URL is not set. Refusing to run "
                    "DB tests without a dedicated local test database.")
    if s.test_database_url == s.database_url:
        pytest.fail("TEST_DATABASE_URL must not equal DATABASE_URL. Refusing to run "
                    "DB tests against the production database.")
    host = sa.engine.make_url(s.test_database_url).host or ""
    if host not in ("localhost", "127.0.0.1"):
        pytest.fail(f"TEST_DATABASE_URL host must be localhost or 127.0.0.1, got "
                    f"{host!r}. Refusing to run DB tests against a non-local database.")
    return s.test_database_url


@pytest.fixture(scope="session")
def _migrated_test_db(live_db_url: str) -> str:
    """Runs the real migrations (0001, 0002) against the local test database once per
    test session, so DB-writing tests exercise the same schema and RLS setup as
    production, not a hand-rolled approximation of it."""
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", live_db_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    return live_db_url


@contextmanager
def transactional_session(db_url: str):
    """The actual mechanism: a connection whose every write -- insert, update,
    delete, truncate -- is undone on exit, even if the caller calls commit(). Runs
    inside an outer transaction; a Session joined to it with join_transaction_mode=
    "create_savepoint" (SQLAlchemy's documented recipe for 'joining a session into an
    external transaction') turns the caller's own commit() into closing a SAVEPOINT
    nested inside the outer one, which is always rolled back, never committed.

    Extracted as a plain context manager (not just a pytest fixture) so
    test_db_fixtures.py's regression test can drive it directly, inside a single
    test, and prove the rollback-on-exit guarantee without depending on pytest's
    fixture-teardown timing or inter-test execution order."""
    engine = sa.create_engine(db_url)
    conn = engine.connect()
    outer_tx = conn.begin()
    session = sa.orm.Session(bind=conn, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()
        engine.dispose()


@pytest.fixture
def db_conn(_migrated_test_db: str):
    with transactional_session(_migrated_test_db) as session:
        yield session
