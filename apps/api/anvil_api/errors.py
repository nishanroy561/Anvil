"""One error shape for every failure the API can produce (plan.md Phase 11 §2).

    {"error": {"code": "...", "message": "..."}}

Routers raise `HTTPException(status, detail={"code", "message"})` and these
handlers do the reshaping, so the envelope is enforced in one place rather than
asked of thirty call sites. That matters because the failures worth having a
consistent shape for are exactly the ones nobody remembers to hand-format:
FastAPI's own 422, a bare `HTTPException(404)` raised by a dependency, and an
unhandled exception.

Before this, the wire carried FastAPI's default `{"detail": …}` — with `detail`
sometimes a dict, sometimes a plain string, and on validation errors a *list* of
per-field objects. Three shapes meant every client had to guess, and the SDKs and
the dashboard each grew their own coercion.

Every response carries an `X-Request-ID` header, and the same id appears in the
server's log line for that request — so a report of "it just failed" can be joined
to what actually happened.

**The id stays in the header and out of the body.** Putting it in the body was the
first attempt and it broke something worth more: Phase 1 makes a wrong password
and an unknown email return *byte-identical* 401s so the endpoint cannot be used
to enumerate accounts, and there is a test comparing the two responses directly.
A random per-request field leaks nothing on its own, but it turns a trivially
auditable invariant into one where every future reader has to re-derive that the
differing field is harmless. The header carries the id just as well.
"""

import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Codes for the statuses FastAPI and Starlette raise on their own, where no
# router chose one. A bare status number is not something a client can branch on.
DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def error_body(code: str, message: str, **extra: Any) -> dict:
    """The body is purely semantic — no request id, no timestamp, nothing that
    varies between two identical failures. See the module docstring."""
    return {"error": {"code": code, "message": message, **extra}}


def _from_detail(status: int, detail: Any) -> tuple[str, str, dict]:
    """Routers pass a dict; everything else passes a string or nothing.

    Extra keys a router set (ingest's per-trace `errors`, for instance) are kept
    alongside `code` and `message` rather than dropped — they are the diagnostic
    payload, and the envelope exists to carry them consistently, not to flatten
    them away.
    """
    if isinstance(detail, dict):
        code = str(detail.get("code") or DEFAULT_CODES.get(status, "error"))
        message = str(detail.get("message") or "")
        extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
        return code, message, extra
    return DEFAULT_CODES.get(status, "error"), str(detail or ""), {}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        """Honour an inbound id so a trace through a proxy keeps one identifier,
        but bound its length — this value ends up in log lines."""
        incoming = (request.headers.get(REQUEST_ID_HEADER) or "").strip()[:64]
        request.state.request_id = incoming or uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code, message, extra = _from_detail(exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message, **extra),
            # 401s carry WWW-Authenticate, 429s carry Retry-After; dropping them
            # here would break the very clients that read them.
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        """422s keep their per-field detail under `error.fields`.

        FastAPI's default puts a *list* at the top level, which is the one shape
        that cannot share an envelope with the others. Flattening it to a
        sentence would throw away the only part an SDK author can act on.
        """
        fields = [
            {
                "path": ".".join(str(p) for p in err.get("loc", ())),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            # Capped: a 500-trace batch of malformed payloads must not produce a
            # response larger than the request that caused it.
            for err in exc.errors()[:20]
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                "invalid_request",
                "Request body or parameters failed validation",
                fields=fields,
                field_count=len(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        """Never leak a traceback to a client; always log one for the operator.

        The request id joins the two: the client has it on the response header,
        the operator has it on this log line.
        """
        log.exception(
            "unhandled error on %s %s (request_id=%s)",
            request.method, request.url.path, request_id(request),
        )
        return JSONResponse(
            status_code=500,
            content=error_body(
                "internal_error",
                "Something went wrong on our side. Quote the X-Request-ID header "
                "if you report this.",
            ),
        )
