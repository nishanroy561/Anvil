"""Auth per plan/architecture.md §5: session cookies for the dashboard,
API keys for SDK ingest/feedback. project_id is always derived from the
resolved credential, never from a request body."""

import hashlib
import secrets
import uuid

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import ApiKey, Project, User

SESSION_COOKIE = "anvil_session"

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd.verify(password, password_hash)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="anvil-session")


def set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    token = _serializer().dumps(str(user_id))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().session_max_age_s,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Login required"})
    try:
        user_id = _serializer().loads(token, max_age=get_settings().session_max_age_s)
    except BadSignature:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Invalid session"})
    user = await session.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Unknown user"})
    return user


# ── API keys ────────────────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, key_prefix, key_hash). Full key is shown exactly once."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(24)
    full = f"ak_{prefix}_{secret}"
    return full, f"ak_{prefix}", hashlib.sha256(full.encode()).hexdigest()


async def current_project_from_api_key(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Project:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ak_"):
        raise HTTPException(401, detail={"code": "invalid_api_key", "message": "API key required"})
    key_hash = hashlib.sha256(header[7:].strip().encode()).hexdigest()
    row = await session.execute(
        select(ApiKey, Project)
        .join(Project, Project.id == ApiKey.project_id)
        .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
    )
    pair = row.first()
    if pair is None:
        raise HTTPException(401, detail={"code": "invalid_api_key", "message": "Unknown or revoked key"})
    return pair[1]
