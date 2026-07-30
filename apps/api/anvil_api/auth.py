"""Auth per plan/architecture.md §5: session cookies for the dashboard,
API keys for SDK ingest/feedback. project_id is always derived from the
resolved credential, never from a request body."""

import hashlib
import secrets
import uuid
from typing import NamedTuple

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
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        # Derived from `web_origin` unless set explicitly. Without it a TLS
        # deployment hands out a cookie a downgrade attack can read in cleartext,
        # and with it hardcoded on, localhost logins silently fail to persist.
        secure=settings.cookie_secure,
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

    # Per-session backstop on dashboard reads (Phase 11 §2). Every other surface
    # already has a limiter of its own; this one covers the dozens of session
    # routes that had none — a runaway poll or a page stuck in a refresh loop is
    # a self-inflicted denial of service that no per-key limit would catch. The
    # ceiling is deliberately far above human use: at 240/min a person clicking
    # around never sees it, and the 10 s auto-refresh on two open tabs doesn't
    # either. Fails open like every other limiter (ADR-0007).
    await enforce_session_read_limit(request, str(user.id))
    return user


async def enforce_session_read_limit(request: Request, user_id: str) -> None:
    """Counts reads only. Writes are individually cheap and already guarded, and
    metering them here would make a limiter the reason a form submission failed.
    """
    if request.method not in ("GET", "HEAD"):
        return

    settings = get_settings()
    if settings.session_read_limit_pm <= 0:  # 0 disables it outright
        return

    # Imported here rather than at module scope: `services.rate_limit` imports
    # from `metrics`, and auth is imported by nearly everything.
    from .redis_client import get_redis
    from .services.rate_limit import enforce_rate_limit

    await enforce_rate_limit(
        get_redis(), "session:reads", user_id, settings.session_read_limit_pm
    )


# ── API keys ────────────────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, key_prefix, key_hash). Full key is shown exactly once."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(24)
    full = f"ak_{prefix}_{secret}"
    return full, f"ak_{prefix}", hashlib.sha256(full.encode()).hexdigest()


class ApiKeyContext(NamedTuple):
    """The resolved credential. Ingest needs the key itself (per-key rate
    limits are the noisy-neighbor guard — one project can hold several keys)
    as well as the project it grants access to."""

    api_key: ApiKey
    project: Project


async def current_api_key_context(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ApiKeyContext:
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
    return ApiKeyContext(api_key=pair[0], project=pair[1])


async def current_project_from_api_key(
    context: ApiKeyContext = Depends(current_api_key_context),
) -> Project:
    return context.project


# ── Either credential ───────────────────────────────────────────────────

class Caller(NamedTuple):
    """Who is asking, however they authenticated (ADR-0021).

    The dashboard sends a session cookie and may reach every project in its
    org; CI sends an API key and may reach exactly the one project that key
    grants. Routes that both need — creating and polling eval runs — resolve
    through this rather than picking one credential and locking the other out.

    Exactly one field is set. `project` being set is itself the authorization:
    an API-key caller is confined to that project by construction, with no
    org-wide reach to get wrong.
    """

    user: User | None = None
    project: Project | None = None


async def current_caller(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Caller:
    """Resolves a session cookie or an `Authorization: Bearer ak_…` key.

    The API key is checked first: a CI job that sends one should never be
    silently downgraded to a stale browser cookie that happens to be present.
    """
    if request.headers.get("authorization", "").lower().startswith("bearer ak_"):
        context = await current_api_key_context(request, session)
        return Caller(project=context.project)
    return Caller(user=await current_user(request, session))
