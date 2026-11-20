"""Pure -- no DB. Stage H clean-clone-test finding: `cp .env.example .env` (as
README instructs) turns TEST_DATABASE_URL's blank line into an env var explicitly
set to "", which pydantic-settings treats as a real value overriding the field's
non-empty default -- not as "absent, use the default". Confirmed by actually
running RUN_DB_TESTS=1 pytest from a fresh clone with .env.example copied
verbatim: every DB-writing test failed with "TEST_DATABASE_URL is not set"."""
from app.config import Settings, _DEFAULT_TEST_DATABASE_URL


def _settings(**env) -> Settings:
    # Bypass the .env file entirely -- these are the two situations that matter:
    # the variable truly absent, vs. present-but-empty (what .env.example produces
    # once copied). _env_file=None stops BaseSettings from also reading a real
    # .env file that might happen to exist in the test runner's cwd.
    return Settings(database_url="postgresql+psycopg://x:y@localhost/z",
                    _env_file=None, **env)


def test_empty_string_falls_back_to_the_default():
    s = _settings(test_database_url="")
    assert s.test_database_url == _DEFAULT_TEST_DATABASE_URL


def test_truly_absent_also_uses_the_default():
    s = _settings()
    assert s.test_database_url == _DEFAULT_TEST_DATABASE_URL


def test_a_real_override_is_still_respected():
    s = _settings(test_database_url="postgresql+psycopg://a:b@127.0.0.1:9999/custom")
    assert s.test_database_url == "postgresql+psycopg://a:b@127.0.0.1:9999/custom"
