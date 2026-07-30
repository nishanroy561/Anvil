import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    clear_session_cookie,
    current_user,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..config import get_settings
from ..db import get_session
from ..models import Organization, Project, User
from ..redis_client import get_redis
from ..schemas.auth import LoginRequest, SignupRequest, UserOut
from ..services.rate_limit import enforce_rate_limit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# Generic message on every auth failure: no user enumeration.
_INVALID = HTTPException(401, detail={"code": "invalid_credentials", "message": "Invalid email or password"})


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


async def _limit_auth(request: Request, email: str) -> None:
    """Per-IP and per-email limits on the auth surface (checklist row 17).

    `request.client.host` is only the real client when nothing sits in front of
    the API. **Behind a reverse proxy it is the proxy**, so every login in the
    deployment shares one bucket and the eleventh person to sign in that minute
    gets a 429 — a self-inflicted outage rather than a weakened control. Uvicorn
    rewrites `request.client` from `X-Forwarded-For`, but only for proxies listed
    in `FORWARDED_ALLOW_IPS`, which defaults to loopback and therefore ignores a
    proxy in another container.

    So: if a forwarded header is present but uvicorn did not act on it, the
    deployment is misconfigured. Warn loudly and fall back to the per-email limit
    alone rather than throttling the whole install on one shared bucket. The
    header is *not* trusted directly — anyone can send it, and doing so would let
    an attacker pick their own bucket and bypass the limit entirely.
    """
    settings = get_settings()
    redis = get_redis()
    ip = request.client.host if request.client else "unknown"

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and forwarded.split(",")[0].strip() != ip:
        log.warning(
            "X-Forwarded-For is present but uvicorn did not apply it (peer=%s). "
            "Set FORWARDED_ALLOW_IPS to your proxy's address, or the per-IP login "
            "limit will meter every user into one bucket. Skipping it for now.",
            ip,
        )
    else:
        await enforce_rate_limit(redis, "auth:ip", ip, settings.auth_ip_limit_pm)

    await enforce_rate_limit(redis, "auth:email", email.lower(), settings.auth_email_limit_pm)


@router.post("/signup", response_model=UserOut, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _limit_auth(request, body.email)
    existing = await session.execute(select(User).where(User.email == body.email.lower()))
    if existing.scalar_one_or_none() is not None:
        # Same shape as any validation failure; don't confirm the address exists.
        raise HTTPException(400, detail={"code": "signup_failed", "message": "Could not create account"})

    org = Organization(name=body.org_name)
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=body.email.lower(), password_hash=hash_password(body.password))
    session.add(user)
    session.add(Project(org_id=org.id, name="Default", slug="default"))
    await session.commit()
    set_session_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _limit_auth(request, body.email)
    row = await session.execute(select(User).where(User.email == body.email.lower()))
    user = row.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise _INVALID
    set_session_cookie(response, user.id)
    return user


@router.post("/logout", status_code=204)
async def logout(response: Response):
    clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user
