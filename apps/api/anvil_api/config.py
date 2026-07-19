from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://anvil:anvil@localhost:5432/anvil"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-change-me"
    deployment_mode: Literal["self_hosted", "cloud"] = "self_hosted"
    web_origin: str = "http://localhost:3000"
    session_max_age_s: int = 7 * 24 * 3600

    # Rate limits (fixed-window; ADR-0007). All per-minute.
    auth_ip_limit_pm: int = 10
    auth_email_limit_pm: int = 5

    # Ingestion (Phase 2)
    ingest_high_water: int = 80_000
    ingest_rate_limit_rpm: int = 300
    ingest_rate_limit_traces_pm: int = 5_000

    # Eval spend guards (Phase 5)
    max_eval_items: int = 500
    max_concurrent_runs: int = 3

    admin_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
