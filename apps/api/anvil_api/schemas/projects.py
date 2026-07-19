import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    name: str = Field(default="default", max_length=256)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    key_prefix: str
    name: str
    created_at: datetime
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    key: str  # full key — returned exactly once at creation
