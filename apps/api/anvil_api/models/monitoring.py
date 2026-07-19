import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, PkUuidMixin


class AlertRule(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "alert_rules"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(256))
    metric: Mapped[str] = mapped_column(String(32))  # cost | latency_p95 | error_rate | eval_score
    comparison: Mapped[str] = mapped_column(String(8))  # gt | lt
    threshold: Mapped[float] = mapped_column(Numeric(14, 6))
    window_minutes: Mapped[int] = mapped_column(Integer, default=15)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertEvent(Base, PkUuidMixin):
    __tablename__ = "alert_events"

    rule_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("alert_rules.id"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Numeric(14, 6))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class MonitorConfig(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "monitor_configs"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(256))
    sample_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.1)
    grader_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    schedule_cron: Mapped[str] = mapped_column(String(64), default="*/10 * * * *")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MonitorScore(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "monitor_scores"

    monitor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("monitor_configs.id"))
    trace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="CASCADE")
    )
    scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
