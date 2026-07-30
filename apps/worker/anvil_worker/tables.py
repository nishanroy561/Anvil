"""SQLAlchemy Core tables for the worker's write path (ADR-0014).

Declarations only — no ORM, no relationships, no metadata.create_all. The API's
Alembic migrations own this schema; these mirror it so the consumer can build
guarded upserts. A column renamed in a migration must be renamed here too; the
round-trip test is what catches it if you forget.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

traces = Table(
    "traces",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, nullable=False),
    Column("name", String(512), nullable=False),
    Column("status", String(16), nullable=False),
    Column("input", JSONB),
    Column("output", JSONB),
    Column("metadata", JSONB),
    Column("search_text", Text),
    Column("model", String(256)),
    Column("total_tokens_in", Integer),
    Column("total_tokens_out", Integer),
    Column("total_cost_usd", Numeric(12, 6)),
    Column("latency_ms", Integer),
    Column("session_id", String(256)),
    Column("user_ref", String(256)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True)),
)

datasets = Table(
    "datasets",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, nullable=False),
    Column("name", String(256), nullable=False),
    Column("description", Text),
    Column("created_at", DateTime(timezone=True)),
)

dataset_items = Table(
    "dataset_items",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("dataset_id", Uuid, nullable=False),
    Column("input", JSONB),
    Column("expected_output", JSONB),
    Column("metadata", JSONB),
    Column("source_trace_id", Uuid),
)

eval_runs = Table(
    "eval_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, nullable=False),
    Column("dataset_id", Uuid, nullable=False),
    Column("name", String(256), nullable=False),
    Column("status", String(16), nullable=False),
    Column("target_config", JSONB),
    Column("grader_config", JSONB),
    Column("item_ids", JSONB),
    Column("progress", Integer),
    Column("total", Integer),
    Column("summary", JSONB),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    # Doubles as the runner heartbeat — see eval_runner.py.
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    Column("created_at", DateTime(timezone=True)),
)

eval_results = Table(
    "eval_results",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("run_id", Uuid, nullable=False),
    Column("item_id", Uuid, nullable=False),
    Column("output", JSONB),
    Column("scores", JSONB),
    Column("judge_reasoning", JSONB),
    Column("latency_ms", Integer),
    Column("cost_usd", Numeric(12, 6)),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True)),
)

spans = Table(
    "spans",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("trace_id", Uuid, nullable=False),
    Column("parent_span_id", Uuid),
    Column("project_id", Uuid, nullable=False),
    Column("name", String(512), nullable=False),
    Column("type", String(32), nullable=False),
    Column("input", JSONB),
    Column("output", JSONB),
    Column("attributes", JSONB),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("cost_usd", Numeric(12, 6)),
    Column("status", String(16), nullable=False),
    Column("error", Text),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True)),
)

feedback = Table(
    "feedback",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, nullable=False),
    Column("trace_id", Uuid, nullable=False),
    Column("rating", SmallInteger, nullable=False),
    Column("comment", Text),
    Column("user_ref", String(256)),
    Column("created_at", DateTime(timezone=True)),
)

# ---- Phase 8: monitors & alerts ---------------------------------------------

monitor_configs = Table(
    "monitor_configs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, nullable=False),
    Column("name", String(256), nullable=False),
    Column("sample_rate", Numeric(5, 4), nullable=False),
    Column("grader_config", JSONB),
    Column("schedule_cron", String(64), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("last_run_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
)

monitor_scores = Table(
    "monitor_scores",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("monitor_id", Uuid, nullable=False),
    Column("trace_id", Uuid, nullable=False),
    Column("scores", JSONB),
    Column("created_at", DateTime(timezone=True)),
)

alert_rules = Table(
    "alert_rules",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("project_id", Uuid, nullable=False),
    Column("name", String(256), nullable=False),
    Column("metric", String(32), nullable=False),
    Column("comparison", String(8), nullable=False),
    Column("threshold", Numeric(14, 6), nullable=False),
    Column("window_minutes", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True)),
)

alert_events = Table(
    "alert_events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("rule_id", Uuid, nullable=False),
    Column("fired_at", DateTime(timezone=True), nullable=False),
    Column("value", Numeric(14, 6), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
    Column("notified", Boolean, nullable=False),
)

projects = Table(
    "projects",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("org_id", Uuid, nullable=False),
    Column("name", String(256), nullable=False),
    Column("slug", String(256)),
    Column("alert_webhook_url", Text),
    Column("created_at", DateTime(timezone=True)),
)
