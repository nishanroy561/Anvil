import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from redis.exceptions import RedisError

from . import errors, tracing
from .config import get_settings
from .metrics import DLQ_DEPTH, STREAM_DEPTH
from .redis_client import get_redis
from .routers import (
    auth,
    datasets,
    eval_runs,
    feedback,
    health,
    ingest,
    monitors,
    projects,
    traces,
    usage,
)
from .streams import DLQ_STREAM, TRACES_STREAM

logging.basicConfig(
    level=logging.INFO,
    # The request id is threaded through error responses and log lines alike, so
    # a report of "it failed" can be joined to the server's own record of it.
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

BACKLOG_REFRESH_S = 10


async def _refresh_backlog_gauges() -> None:
    """The API exports queue depth, not the worker (ADR-0009): a dead worker
    can't report the backlog it isn't draining. This loop is the only thing
    that distinguishes 'stuck' from 'dead' in the alert rules."""
    redis = get_redis()
    while True:
        try:
            STREAM_DEPTH.set(await redis.xlen(TRACES_STREAM))
            DLQ_DEPTH.set(await redis.xlen(DLQ_STREAM))
        except RedisError:
            log.warning("backlog gauge refresh failed; Redis unavailable")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("backlog gauge refresh failed unexpectedly")
        await asyncio.sleep(BACKLOG_REFRESH_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_refresh_backlog_gauges())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Anvil API", version="0.1.0", lifespan=lifespan)

# One error envelope and one request id for everything below (Phase 11 §2).
errors.install(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# /metrics for Prometheus (plan/observability.md §1).
Instrumentator().instrument(app).expose(app)

# OTLP traces for Tempo (plan/observability.md §4). Inert unless
# OTEL_EXPORTER_OTLP_ENDPOINT is set, so this costs nothing by default.
tracing.setup(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(ingest.router)
app.include_router(traces.router)
app.include_router(datasets.router)
app.include_router(eval_runs.router)
app.include_router(monitors.router)
app.include_router(feedback.router)
app.include_router(usage.router)
