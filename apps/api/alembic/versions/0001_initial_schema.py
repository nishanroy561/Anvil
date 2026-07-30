"""Initial schema — all tables and indexes up front (plan/architecture.md §4).

Later phases touch code, not schema. The special pieces:
- pg_trgm extension + trigram GIN on traces.search_text (ADR-0005)
- GIN jsonb_path_ops on traces.metadata only — span payloads deliberately unindexed (ADR-0004)
- unique (run_id, item_id) on eval_results — eval-run resume idempotency
- partial unique (trace_id, user_ref) on feedback — repeat feedback upserts
- ON DELETE CASCADE from traces to spans/feedback/monitor_scores; SET NULL on
  dataset_items.source_trace_id (retention safety — plan/retention.md §1)

Revision ID: 0001
Revises:
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("plan_overrides", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("slug", sa.String(256), nullable=False),
        sa.Column("alert_webhook_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False, server_default="default"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "traces",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("search_text", sa.Text, nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("total_tokens_in", sa.Integer, nullable=True),
        sa.Column("total_tokens_out", sa.Integer, nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("session_id", sa.String(256), nullable=True),
        sa.Column("user_ref", sa.String(256), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_traces_project_started", "traces",
        ["project_id", sa.text("started_at DESC")],
    )
    op.create_index("ix_traces_project_status", "traces", ["project_id", "status"])
    op.create_index(
        "ix_traces_metadata_gin", "traces", ["metadata"],
        postgresql_using="gin", postgresql_ops={"metadata": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_traces_search_trgm", "traces", ["search_text"],
        postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"},
    )

    op.create_table(
        "spans",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("trace_id", sa.Uuid, sa.ForeignKey("traces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_span_id", sa.Uuid, nullable=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("attributes", JSONB, nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_spans_trace", "spans", ["trace_id"])
    op.create_index("ix_spans_project_started", "spans", ["project_id", "started_at"])

    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "dataset_items",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("dataset_id", sa.Uuid, sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("expected_output", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("source_trace_id", sa.Uuid, sa.ForeignKey("traces.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("dataset_id", sa.Uuid, sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("target_config", JSONB, nullable=True),
        sa.Column("grader_config", JSONB, nullable=True),
        sa.Column("item_ids", JSONB, nullable=True),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("eval_runs.id"), nullable=False),
        sa.Column("item_id", sa.Uuid, sa.ForeignKey("dataset_items.id"), nullable=False),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("scores", JSONB, nullable=True),
        sa.Column("judge_reasoning", JSONB, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_eval_results_run", "eval_results", ["run_id"])
    op.create_index("uq_eval_results_run_item", "eval_results", ["run_id", "item_id"], unique=True)

    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("trace_id", sa.Uuid, sa.ForeignKey("traces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.SmallInteger, nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("user_ref", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedback_trace", "feedback", ["trace_id"])
    op.create_index(
        "uq_feedback_trace_user", "feedback", ["trace_id", "user_ref"],
        unique=True, postgresql_where=sa.text("user_ref IS NOT NULL"),
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("comparison", sa.String(8), nullable=False),
        sa.Column("threshold", sa.Numeric(14, 6), nullable=False),
        sa.Column("window_minutes", sa.Integer, nullable=False, server_default="15"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("rule_id", sa.Uuid, sa.ForeignKey("alert_rules.id"), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(14, 6), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "monitor_configs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("sample_rate", sa.Numeric(5, 4), nullable=False, server_default="0.1"),
        sa.Column("grader_config", JSONB, nullable=True),
        sa.Column("schedule_cron", sa.String(64), nullable=False, server_default="*/10 * * * *"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "monitor_scores",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("monitor_id", sa.Uuid, sa.ForeignKey("monitor_configs.id"), nullable=False),
        sa.Column("trace_id", sa.Uuid, sa.ForeignKey("traces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scores", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "org_usage",
        sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("month", sa.Date, primary_key=True),
        sa.Column("traces_ingested", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("eval_items", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("storage_bytes", sa.BigInteger, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for table in [
        "org_usage", "monitor_scores", "monitor_configs", "alert_events", "alert_rules",
        "feedback", "eval_results", "eval_runs", "dataset_items", "datasets",
        "spans", "traces", "api_keys", "projects", "users", "organizations",
    ]:
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
