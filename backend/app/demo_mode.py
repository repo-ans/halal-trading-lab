"""The demo environment (CLAUDE.md) is isolated by never letting production and
the demo database be confused for each other. One function enforces that in both
directions, and is the ONLY place either rule may live -- main.py calls it at
import time (before the app is even constructed) and scripts/seed_demo.py calls
it before touching its target connection at all."""
import sqlalchemy as sa

DEMO_DB_NAME = "halal_demo"
DEMO_DB_PORT = 5434
DEMO_DB_HOSTS = ("localhost", "127.0.0.1")


class DemoModeUnsafe(Exception):
    pass


def assert_demo_db_is_safe(settings) -> None:
    """app_mode="demo": DATABASE_URL must be localhost/127.0.0.1, port 5434, and
    database name "halal_demo" -- ALL three, not just the host, so a demo server
    can never be pointed at a differently-named local database by accident.
    Any other app_mode: refuses if DATABASE_URL's database name is "halal_demo"
    -- the converse mistake (running in production mode against the demo
    database) is just as unsafe and just as easy to catch the same way."""
    url = sa.engine.make_url(settings.database_url)

    if settings.app_mode == "demo":
        if (url.host not in DEMO_DB_HOSTS or url.port != DEMO_DB_PORT
                or url.database != DEMO_DB_NAME):
            raise DemoModeUnsafe(
                f"APP_MODE=demo requires DATABASE_URL to point at "
                f"{DEMO_DB_HOSTS[0]}:{DEMO_DB_PORT}/{DEMO_DB_NAME} -- got "
                f"{url.host}:{url.port}/{url.database}")
        return

    if url.database == DEMO_DB_NAME:
        raise DemoModeUnsafe(
            f"APP_MODE={settings.app_mode!r} but DATABASE_URL's database is "
            f"{DEMO_DB_NAME!r} -- refusing to run against the demo database "
            "outside demo mode")
