import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .config import get_settings
from .routers import auth, health, projects

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Anvil API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# /metrics for Prometheus (plan/observability.md §1). Backlog gauges
# (anvil_stream_depth, anvil_dlq_depth) get registered here in Phase 2.
Instrumentator().instrument(app).expose(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
