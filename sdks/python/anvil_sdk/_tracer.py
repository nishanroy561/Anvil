"""Trace/span context managers and the @observe decorator.

Nesting rides on `contextvars`, so it works unchanged across threads, asyncio
tasks, and the sync/async mix a typical FastAPI app has. Nothing here raises on
Anvil's behalf: instrumentation failures are swallowed, but exceptions from the
*host's* own code are always re-raised after being recorded.
"""

from __future__ import annotations

import functools
import inspect
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional

from ._wire import Span, Trace

log = logging.getLogger("anvil_sdk")

_current_trace: ContextVar[Optional[Trace]] = ContextVar("anvil_trace", default=None)
_current_span: ContextVar[Optional[Span]] = ContextVar("anvil_span", default=None)


def current_trace() -> Optional[Trace]:
    return _current_trace.get()


def current_span() -> Optional[Span]:
    return _current_span.get()


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]


@contextmanager
def trace(
    name: str,
    input: Any = None,
    metadata: Optional[dict] = None,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    user_ref: Optional[str] = None,
    client: Any = None,
) -> Iterator[Trace]:
    """Root of a unit of work. `trace.id` is a client-generated UUID, exposed so
    callers can round-trip it to their frontend for feedback later."""
    from . import get_client

    obj = Trace(
        name=name,
        input=input,
        metadata=dict(metadata or {}),
        model=model,
        session_id=session_id,
        user_ref=user_ref,
    )
    token = _current_trace.set(obj)
    span_token = _current_span.set(None)
    try:
        yield obj
    except BaseException as exc:
        obj.status = "error"
        obj.metadata.setdefault("error", _describe(exc))
        raise
    finally:
        _current_trace.reset(token)
        _current_span.reset(span_token)
        try:
            if obj.ended_at is None:
                obj.end(status=obj.status if obj.status == "error" else "ok")
            target = client or get_client()
            if target is not None:
                target.submit(obj)
        except Exception:  # noqa: BLE001
            log.debug("anvil: failed to submit trace", exc_info=True)


@contextmanager
def span(
    name: str,
    type: str = "custom",
    input: Any = None,
    attributes: Optional[dict] = None,
) -> Iterator[Span]:
    """A step inside a trace. Nests under whatever span is active, if any.

    With no active trace the span becomes its own single-span trace rather than
    being dropped — a decorated helper called outside a request still produces
    something you can look at.
    """
    parent = _current_span.get()
    owner = _current_trace.get()

    implicit: Optional[Any] = None
    if owner is None:
        implicit = trace(name, input=input)
        owner = implicit.__enter__()

    obj = Span(
        name=name,
        type=type,
        input=input,
        attributes=dict(attributes or {}),
        parent_span_id=parent.id if parent else None,
    )
    owner.spans.append(obj)
    token = _current_span.set(obj)
    try:
        yield obj
    except BaseException as exc:
        obj.error = _describe(exc)
        obj.status = "error"
        raise
    finally:
        _current_span.reset(token)
        if obj.ended_at is None:
            obj.end(status="error" if obj.error else "ok")
        if implicit is not None:
            owner.output = obj.output
            implicit.__exit__(None, None, None)


def start_trace(name: str, **kwargs: Any) -> Trace:
    """Manual API for code that can't use a `with` block (request middleware,
    callbacks). Call `.end(output=…)` to close and submit it."""
    from . import get_client

    obj = Trace(name=name, **kwargs)
    token = _current_trace.set(obj)
    span_token = _current_span.set(None)

    def finish(_: Trace) -> None:
        try:
            _current_trace.reset(token)
            _current_span.reset(span_token)
        except ValueError:
            # Ended from a different context than it started in — the contextvar
            # will fall out of scope on its own; submitting still matters.
            pass
        target = get_client()
        if target is not None:
            target.submit(obj)

    obj._on_end = finish
    return obj


def start_span(name: str, type: str = "custom", **kwargs: Any) -> Optional[Span]:
    """Manual counterpart to `span()`. Returns None if no trace is active."""
    owner = _current_trace.get()
    if owner is None:
        return None
    parent = _current_span.get()
    obj = Span(
        name=name, type=type, parent_span_id=parent.id if parent else None, **kwargs
    )
    owner.spans.append(obj)
    token = _current_span.set(obj)

    def finish(_: Span) -> None:
        try:
            _current_span.reset(token)
        except ValueError:
            pass

    obj._on_end = finish
    return obj


def _capture_args(func: Callable, args: tuple, kwargs: dict) -> Any:
    """Bind call arguments by name for the span input. `self` is dropped — it is
    noise in a waterfall and often unserializable."""
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        captured = dict(bound.arguments)
        captured.pop("self", None)
        captured.pop("cls", None)
        return captured or None
    except Exception:  # noqa: BLE001
        return None


def observe(
    _func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    type: str = "custom",
    capture_input: bool = True,
    capture_output: bool = True,
):
    """Decorator sugar: `@observe(type="llm")`. Works on sync and async
    functions; args become the span input, the return value its output, and an
    exception marks the span errored before propagating."""

    def decorate(func: Callable) -> Callable:
        span_name = name or getattr(func, "__name__", "anonymous")

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                payload = _capture_args(func, args, kwargs) if capture_input else None
                with span(span_name, type=type, input=payload) as active:
                    result = await func(*args, **kwargs)
                    if capture_output:
                        active.output = result
                    return result

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            payload = _capture_args(func, args, kwargs) if capture_input else None
            with span(span_name, type=type, input=payload) as active:
                result = func(*args, **kwargs)
                if capture_output:
                    active.output = result
                return result

        return wrapper

    return decorate(_func) if callable(_func) else decorate
