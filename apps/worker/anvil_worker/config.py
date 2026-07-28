"""Worker settings. Reads the same environment as the API (`.env.example`),
but the worker declares only what it uses — it shares no code with the API
package (ADR-0014)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://anvil:anvil@localhost:5432/anvil"
    redis_url: str = "redis://localhost:6379/0"
    deployment_mode: str = "self_hosted"

    # Consumer batching: whichever comes first (plan/ingestion.md §3).
    consumer_batch_size: int = 500
    consumer_block_ms: int = 200

    # A message redelivered this many times is poison — route it to the DLQ
    # rather than letting it crash-loop every replica (checklist row 4).
    max_deliveries: int = 5
    reclaim_idle_ms: int = 60_000
    reclaim_interval_s: int = 30
    dlq_maxlen: int = 10_000

    # Stale-trace reaper (checklist row 3).
    reaper_interval_s: int = 300
    stale_trace_minutes: int = 30

    # Eval runner (Phase 5, plan/evaluation.md §3).
    eval_item_concurrency: int = 5
    # plugins.md §3 budgets one timeout for graders, but a string comparison and
    # a model round trip are not the same workload: 30 s timed out ~30% of real
    # judge calls in acceptance testing, which reads as "the judge is flaky"
    # rather than "our budget was wrong". Judges get their own.
    grader_timeout_s: int = 30
    judge_timeout_s: int = 120
    # The runner writes updated_at as a heartbeat; a run whose heartbeat is
    # older than this is presumed orphaned and gets resumed by another worker.
    eval_heartbeat_s: int = 15
    eval_stale_run_s: int = 120
    eval_reclaim_interval_s: int = 60

    metrics_port: int = 9101


@lru_cache
def get_settings() -> Settings:
    return Settings()
