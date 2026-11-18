from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_SessionLocal = None


def _init() -> None:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """For callers outside FastAPI's dependency injection -- the scheduler, scripts."""
    _init()
    with _SessionLocal() as session:
        yield session


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


@contextmanager
def read_only_session(url: str) -> Iterator[Session]:
    """A session on an INDEPENDENT connection to `url` (never the app's own
    DATABASE_URL/engine above), with `SET TRANSACTION READ ONLY` as its first
    statement -- the database itself, not just application code, refuses any
    write for the rest of the transaction. For one-off reads against a database
    other than the app's own: scripts/preview_signals.py's symbol/date preview,
    scripts/seed_demo.py's copy-from-production step."""
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            yield session
    finally:
        engine.dispose()
