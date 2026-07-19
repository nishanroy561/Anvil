import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, PkUuidMixin


class Trace(Base, PkUuidMixin, CreatedAtMixin):
    """id is client-generated (idempotency key). Writes go through guarded
    upserts — tenant guard + monotonic completion guard (ADR-0003)."""

    __tablename__ = "traces"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    total_tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Span(Base, PkUuidMixin):
    __tablename__ = "spans"

    trace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="CASCADE")
    )
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(512))
    type: Mapped[str] = mapped_column(String(32))
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Feedback(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "feedback"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    trace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="CASCADE")
    )
    rating: Mapped[int] = mapped_column(SmallInteger)  # -1 thumbs-down / +1 thumbs-up
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
