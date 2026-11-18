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

    # Comma-separated Supabase auth user UUIDs. Empty/unset means NO ONE is an admin
    # (fail closed) -- never treat an unset list as "everyone" or "no restriction".
    admin_user_ids: str = ""
    # UNCONFIRMED ASSUMPTION (CLAUDE.md section 5): 90 days, owner has not confirmed.
    compliance_default_validity_days: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_user_id_set(self) -> set[str]:
        return {u.strip().lower() for u in self.admin_user_ids.split(",") if u.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
