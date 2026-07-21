"""A tiny FastAPI RAG app instrumented with Anvil — the whole integration is
the `anvil.init()` call below plus passing `AnvilCallbackHandler` in the
config. That is the "<10 lines" claim in plan/sdk.md §1.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

import anvil_sdk as anvil
from anvil_sdk.langchain import AnvilCallbackHandler

from .chain import build_chain

chain = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    anvil.init(
        api_key=os.getenv("ANVIL_API_KEY", "ak_demo"),
        base_url=os.getenv("ANVIL_BASE_URL", "http://localhost:8000"),
        # Scrub PII before it leaves this process (plan/sdk.md §1). Swap in
        # your own matcher; this one is deliberately simplistic.
        redact=_redact,
    )
    global chain
    chain = build_chain()
    yield
    anvil.shutdown()


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _redact(obj):
    """Runs over every trace and span before anything leaves this process.
    Return the object to keep it, or None to drop it entirely.

    Note the recursion: payloads are rarely flat strings — a span input is
    typically a dict and a retrieval output a list of documents, so a hook that
    only checks top-level strings leaks exactly the data it was added to catch.
    """
    for field in ("input", "output", "metadata", "attributes"):
        value = getattr(obj, field, None)
        if value is not None:
            setattr(obj, field, _mask(value))
    return obj


def _mask(value):
    if isinstance(value, str):
        return EMAIL.sub("[email redacted]", value)
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    return value


app = FastAPI(title="Anvil demo RAG app", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None
    user_ref: str | None = None


@app.post("/ask")
def ask(request: AskRequest) -> dict:
    handler = AnvilCallbackHandler(
        trace_name="ask",
        session_id=request.session_id,
        user_ref=request.user_ref,
        metadata={"source": "demo-api"},
    )
    result = chain.invoke(request.question, config={"callbacks": [handler]})
    # The trace id goes back to the caller so a frontend can attach feedback to
    # it later (plan/sdk.md §1; the feedback endpoint arrives in Phase 9).
    return {**result, "trace_id": handler.trace.id if handler.trace else None}


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "anvil": anvil.stats()}
