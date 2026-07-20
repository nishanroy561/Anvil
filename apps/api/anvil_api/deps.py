"""Shared route dependencies.

Tenant scoping lives here so every router resolves a project the same way —
one place to audit for the "404, never 403" rule.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Project, User

NOT_FOUND = HTTPException(404, detail={"code": "not_found", "message": "Not found"})


async def owned_project(
    project_id: uuid.UUID, user: User, session: AsyncSession
) -> Project:
    """Resolves a project inside the caller's org.

    A project that exists but belongs to another org is indistinguishable from
    one that doesn't exist — the 404 is the point, not an accident.
    """
    project = await session.get(Project, project_id)
    if project is None or project.org_id != user.org_id:
        raise NOT_FOUND
    return project
