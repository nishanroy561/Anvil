import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, PkUuidMixin


class Dataset(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "datasets"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class DatasetItem(Base, PkUuidMixin):
    __tablename__ = "dataset_items"

    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("datasets.id"))
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expected_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # SET NULL: items copy their data, so they survive retention deleting the trace.
    source_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="SET NULL"), nullable=True
    )


class EvalRun(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "eval_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("datasets.id"))
    name: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    target_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    grader_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    item_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # snapshot at run start
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Doubles as the runner heartbeat (crash recovery — plan/evaluation.md §3).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvalResult(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "eval_results"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("eval_runs.id"))
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("dataset_items.id"))
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    judge_reasoning: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
