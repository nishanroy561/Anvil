import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, generate_api_key
from ..db import get_session
from ..models import ApiKey, Project, User
from ..schemas.projects import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(prefix="/v1", tags=["projects"])

# Tenant behavior: resources outside the caller's org 404 (never 403).
_NOT_FOUND = HTTPException(404, detail={"code": "not_found", "message": "Not found"})


async def _owned_project(
    project_id: uuid.UUID, user: User, session: AsyncSession
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.org_id != user.org_id:
        raise _NOT_FOUND
    return project


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.created_at)
    )
    return list(rows.scalars())


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "project"
    project = Project(org_id=user.org_id, name=body.name, slug=slug)
    session.add(project)
    await session.commit()
    return project


@router.post("/projects/{project_id}/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    project_id: uuid.UUID,
    body: ApiKeyCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _owned_project(project_id, user, session)
    full, prefix, key_hash = generate_api_key()
    key = ApiKey(project_id=project.id, key_hash=key_hash, key_prefix=prefix, name=body.name)
    session.add(key)
    await session.commit()
    return ApiKeyCreated(
        id=key.id, key_prefix=key.key_prefix, name=key.name,
        created_at=key.created_at, revoked_at=None, key=full,
    )


@router.get("/projects/{project_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _owned_project(project_id, user, session)
    rows = await session.execute(
        select(ApiKey).where(ApiKey.project_id == project.id).order_by(ApiKey.created_at)
    )
    return list(rows.scalars())


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.execute(
        select(ApiKey, Project)
        .join(Project, Project.id == ApiKey.project_id)
        .where(ApiKey.id == key_id, Project.org_id == user.org_id)
    )
    pair = row.first()
    if pair is None:
        raise _NOT_FOUND
    pair[0].revoked_at = datetime.now(timezone.utc)
    await session.commit()
