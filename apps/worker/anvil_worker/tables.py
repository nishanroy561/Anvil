"""SQLAlchemy Core tables for the worker's write path (ADR-0014).

Declarations only — no ORM, no relationships, no metadata.create_all. The API's
Alembic migrations own this schema; these mirror it so the consumer can build
guarded upserts. A column renamed in a migration must be renamed here too; the
round-trip test is what catches it if you forget.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
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
