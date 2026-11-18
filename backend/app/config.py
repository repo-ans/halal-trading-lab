from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # The throwaway local Postgres (docker-compose service db_test, profile "test")
    # that DB-writing tests run against. Never the production project -- conftest.py
    # refuses to run DB tests if this equals database_url or is not on localhost.
    test_database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5433/halal_test"
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    cors_origins: str = "http://localhost:5173"
    app_env: str = "development"

    # CLAUDE.md section 3: yfinance is not licensed for production use. YFinanceProvider
    # refuses to run unless this is explicitly true.
    allow_dev_data_source: bool = False
    # Shared secret for POST /jobs/backfill. Empty means the endpoint refuses all
    # requests with 503, never falls back to "no auth required".
    job_secret: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
