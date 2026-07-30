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

    # Feedback consumer (Phase 9, plan/evaluation.md §7). A thumbs-down can
    # overtake the trace it points at, so rows whose trace is missing are
    # deferred and retried; after the window they are dead-lettered rather than
    # retried forever (checklist row 12).
    feedback_batch_size: int = 200
    feedback_retry_window_s: int = 600
    feedback_retry_max_delay_s: int = 30
    # Ceiling on the deferred set. Feedback naming trace ids that will never
    # exist is the abuse shape here, and an unbounded retry set would let it
    # grow until Redis is the outage.
    feedback_deferred_max: int = 50_000

    # Retention (Phase 10, plan/retention.md §1). **Unset means keep everything**
    # — an operator who has not asked for deletion never gets deletion, so this
    # is `None` rather than a number, and no default window is inferred.
    retention_days: int | None = None
    # Traces deleted per transaction. Never one giant DELETE: that bloats WAL and
    # holds locks for the length of the sweep (checklist row 23).
    retention_batch_size: int = 5_000
    retention_batch_sleep_s: float = 0.5
    retention_interval_s: int = 24 * 3600

    metrics_port: int = 9101


@lru_cache
def get_settings() -> Settings:
    return Settings()
