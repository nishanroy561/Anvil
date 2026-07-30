from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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
    # Backstop on dashboard reads, per session (Phase 11 §2). Far above human
    # use — a page stuck in a refresh loop is what this catches, not a person.
    # 0 disables it.
    session_read_limit_pm: int = 240

    # Ingestion (Phase 2)
    ingest_high_water: int = 80_000
    # 20k of headroom over the high-water mark so approximate trimming never
    # eats unprocessed entries (plan/ingestion.md §2).
    ingest_stream_maxlen: int = 100_000
    ingest_rate_limit_rpm: int = 300
    ingest_rate_limit_traces_pm: int = 5_000
    ingest_max_body_bytes: int = 5 * 1024 * 1024
    ingest_max_field_bytes: int = 100 * 1024
    # Dashboard free-text search: trigram floor and server-enforced time bound.
    search_min_chars: int = 3
    search_default_days: int = 7
    search_max_days: int = 30
    traces_page_size_max: int = 200

    # Eval spend guards (Phase 5)
    max_eval_items: int = 500
    max_concurrent_runs: int = 3

    # Feedback (Phase 9). Reachable from a customer's own end users, which makes
    # it the most abuse-exposed surface after ingest (checklist row 19).
    feedback_rate_limit_pm: int = 600
    feedback_max_comment_chars: int = 4_000
    feedback_stream_maxlen: int = 100_000
    failure_inbox_page_size_max: int = 200

    # Retention (Phase 10). The **worker** owns deletion; the API only reports the
    # window, because "12,000 traces" means something different under a 30-day
    # policy than under keep-forever. Unset = keep everything.
    retention_days: int | None = None

    admin_api_key: str = ""

    @field_validator("retention_days", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        """`RETENTION_DAYS=` is unset, not a parse error.

        Found by `docker compose up` on a clean volume: compose renders
        `${RETENTION_DAYS:-}` as an empty string and passes it in, and pydantic
        refused to read `""` as `int | None` — so the API crash-looped at import
        on the *default* configuration. `.env.example` ships the same blank value,
        so anyone starting the API from the repo root hit it too.

        An optional integer whose documented default is "unset" has to treat an
        empty value as unset; anything else makes the documented default a
        landmine.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
