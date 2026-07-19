import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, PkUuidMixin


class Organization(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(256))
    plan: Mapped[str] = mapped_column(String(32), default="free", server_default="free")
    plan_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class User(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "users"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))


class Project(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "projects"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(256))
    slug: Mapped[str] = mapped_column(String(256))
    alert_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApiKey(Base, PkUuidMixin, CreatedAtMixin):
    __tablename__ = "api_keys"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(256), default="default")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgUsage(Base):
    __tablename__ = "org_usage"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), primary_key=True
    )
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    traces_ingested: Mapped[int] = mapped_column(BigInteger, default=0)
    eval_items: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
