"""Shared route dependencies.

Tenant scoping lives here so every router resolves a project the same way —
one place to audit for the "404, never 403" rule.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Caller
from .models import Project, User

NOT_FOUND = HTTPException(404, detail={"code": "not_found", "message": "Not found"})


async def owned_project(
    project_id: uuid.UUID, caller: Caller | User, session: AsyncSession
) -> Project:
    """Resolves a project the caller is allowed to reach.

    A project that exists but belongs to someone else is indistinguishable from
    one that doesn't exist — the 404 is the point, not an accident. That holds
    for both credentials: a session reaching outside its org and an API key
    reaching outside its project get the same answer.

    Accepts a bare `User` as well as a `Caller` so routes that are
    session-only (projects, API keys, traces) did not all have to change when
    API-key access was added to the eval surface (ADR-0021).
    """
    if isinstance(caller, User):
        caller = Caller(user=caller)

    project = await session.get(Project, project_id)
    if project is None:
        raise NOT_FOUND

    if caller.project is not None:
        # An API key grants exactly one project. No org-wide reach to get wrong.
        if project.id != caller.project.id:
            raise NOT_FOUND
    elif caller.user is None or project.org_id != caller.user.org_id:
        raise NOT_FOUND

    return project
