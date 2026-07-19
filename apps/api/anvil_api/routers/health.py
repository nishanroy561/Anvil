from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..redis_client import get_redis

router = APIRouter()


@router.get("/healthz")
async def healthz(response: Response, session: AsyncSession = Depends(get_session)):
    db_ok, redis_ok = False, False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    try:
        await get_redis().ping()
        redis_ok = True
    except Exception:
        pass
    if not (db_ok and redis_ok):
        response.status_code = 503
    return {"db": db_ok, "redis": redis_ok}
