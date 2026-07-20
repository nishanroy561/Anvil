from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from .config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """One transaction per persisted batch: either the whole batch lands or
    none of it does, so a redelivery after a crash replays cleanly."""
    async with engine.begin() as conn:
        yield conn
