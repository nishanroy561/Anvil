"""LangChain integration — instrument a chain in one line.

    from anvil_sdk.langchain import AnvilCallbackHandler
    chain.invoke(question, config={"callbacks": [AnvilCallbackHandler()]})

LangChain hands us a run tree keyed by `run_id`/`parent_run_id`, so this handler
keeps its own id->span map rather than using the contextvar stack: callbacks for
concurrent branches interleave, and the explicit ids are the only reliable
parentage. The top-level run (no parent) becomes the trace.

Every callback is wrapped: a bug in here must not break the customer's chain,
so a failed callback loses a span, not the request (ADR-0013).
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from ._wire import Span, Trace

log = logging.getLogger("anvil_sdk")

try:  # langchain is an optional extra: `pip install anvil-sdk[langchain]`
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover - exercised only without langchain
    class BaseCallbackHandler:  # type: ignore[no-redef]
        """Stand-in so importing this module without langchain fails only at use."""


def _safe(method):
    """Swallow anything a callback raises — including our own bugs."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:  # noqa: BLE001
            log.debug("anvil: langchain callback %s failed", method.__name__, exc_info=True)
            return None

    return wrapper


def _text(value: Any) -> Any:
    """LangChain passes Documents, BaseMessages, and raw strings; keep them
    readable in the waterfall without dragging in langchain types."""
    if isinstance(value, list):
        return [_text(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _text(v) for k, v in value.items()}
    content = getattr(value, "content", None)
    if content is not None:
        return content
    page = getattr(value, "page_content", None)
    if page is not None:
        return {"text": page, "metadata": getattr(value, "metadata", {}) or {}}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _model_name(serialized: Optional[dict], kwargs: dict) -> Optional[str]:
    params = kwargs.get("invocation_params") or {}
    for source, key in (
        (params, "model"),
        (params, "model_name"),
        ((serialized or {}).get("kwargs") or {}, "model"),
        ((serialized or {}).get("kwargs") or {}, "model_name"),
    ):
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    name = (serialized or {}).get("name")
    return name if isinstance(name, str) else None


def _usage(response: Any) -> tuple[Optional[int], Optional[int]]:
    """Token counts moved between LLMResult.llm_output and per-generation
    usage_metadata across langchain versions; check both."""
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    tokens_in = usage.get("prompt_tokens") or usage.get("input_tokens")
    tokens_out = usage.get("completion_tokens") or usage.get("output_tokens")
    if tokens_in is not None or tokens_out is not None:
        return tokens_in, tokens_out

    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            meta = getattr(message, "usage_metadata", None) or {}
            if meta:
                return meta.get("input_tokens"), meta.get("output_tokens")
    return None, None


class AnvilCallbackHandler(BaseCallbackHandler):
    """Maps LangChain runs onto Anvil traces and spans."""

    def __init__(
        self,
        trace_name: Optional[str] = None,
        session_id: Optional[str] = None,
        user_ref: Optional[str] = None,
        metadata: Optional[dict] = None,
        client: Any = None,
    ) -> None:
        self.trace_name = trace_name
        self.session_id = session_id
        self.user_ref = user_ref
        self.metadata = dict(metadata or {})
        self._client = client
        self._spans: Dict[UUID, Span] = {}
        self._trace: Optional[Trace] = None
        self._root_run: Optional[UUID] = None

    # ---- plumbing ------------------------------------------------------

    @property
    def trace(self) -> Optional[Trace]:
        """The trace built by the last (or in-flight) run — `handler.trace.id`
        is how a caller round-trips the id to their frontend for feedback."""
        return self._trace

    def _open(
        self,
        run_id: UUID,
        parent_run_id: Optional[UUID],
        name: str,
        span_type: str,
        payload: Any,
        attributes: Optional[dict] = None,
    ) -> Span:
        if self._trace is None or self._root_run is None:
            self._trace = Trace(
                name=self.trace_name or name,
                input=payload,
                metadata=dict(self.metadata),
                session_id=self.session_id,
                user_ref=self.user_ref,
            )
            self._root_run = run_id

        parent = self._spans.get(parent_run_id) if parent_run_id else None
        span = Span(
            name=name,
            type=span_type,
            input=payload,
            attributes=dict(attributes or {}),
            parent_span_id=parent.id if parent else None,
        )
        self._spans[run_id] = span
        self._trace.spans.append(span)
        return span

    def _close(
        self, run_id: UUID, output: Any = None, error: Optional[str] = None, **fields: Any
    ) -> None:
        span = self._spans.pop(run_id, None)
        if span is not None:
            span.end(output=output, error=error, **fields)
        if run_id == self._root_run:
            self._finish(output=output, error=error)

    def _finish(self, output: Any = None, error: Optional[str] = None) -> None:
        """Root run finished: close and ship the trace."""
        from . import get_client

        # `self._trace` is deliberately kept so `handler.trace.id` still reads
        # after the run; clearing `_root_run` is what makes the next run start
        # a fresh trace.
        trace = self._trace
        self._root_run = None
        self._spans.clear()
        if trace is None:
            return
        trace.end(output=output, status="error" if error else "ok")
        if error:
            trace.metadata["error"] = error
        target = self._client or get_client()
        if target is not None:
            target.submit(trace)

    # ---- chains --------------------------------------------------------

    @_safe
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name") or "chain"
        self._open(run_id, parent_run_id, name, "chain", _text(inputs))

    @_safe
    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._close(run_id, output=_text(outputs))

    @_safe
    def on_chain_error(self, error, *, run_id, **kwargs):
        self._close(run_id, error=str(error)[:2000])

    # ---- LLMs ----------------------------------------------------------

    @_safe
    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        model = _model_name(serialized, kwargs)
        span = self._open(
            run_id, parent_run_id, model or "llm", "llm", _text(prompts),
            attributes={"model": model, **(kwargs.get("invocation_params") or {})},
        )
        # The trace's model column drives cost roll-ups and the model filter;
        # first LLM in the trace wins.
        if self._trace is not None and self._trace.model is None:
            self._trace.model = model
        span.attributes.setdefault("model", model)

    @_safe
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self.on_llm_start(
            serialized, _text(messages), run_id=run_id, parent_run_id=parent_run_id, **kwargs
        )

    @_safe
    def on_llm_end(self, response, *, run_id, **kwargs):
        tokens_in, tokens_out = _usage(response)
        generations = getattr(response, "generations", None) or []
        flattened = [_text(g) for batch in generations for g in batch]
        output = flattened[0] if len(flattened) == 1 else flattened
        self._close(run_id, output=output, tokens_in=tokens_in, tokens_out=tokens_out)

    @_safe
    def on_llm_error(self, error, *, run_id, **kwargs):
        self._close(run_id, error=str(error)[:2000])

    # ---- retrievers ----------------------------------------------------

    @_safe
    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kwargs):
        name = (serialized or {}).get("name") or "retriever"
        self._open(run_id, parent_run_id, name, "retrieval", query)

    @_safe
    def on_retriever_end(self, documents, *, run_id, **kwargs):
        self._close(run_id, output=_text(documents))

    @_safe
    def on_retriever_error(self, error, *, run_id, **kwargs):
        self._close(run_id, error=str(error)[:2000])

    # ---- tools ---------------------------------------------------------

    @_safe
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        name = (serialized or {}).get("name") or "tool"
        self._open(run_id, parent_run_id, name, "tool", input_str)

    @_safe
    def on_tool_end(self, output, *, run_id, **kwargs):
        self._close(run_id, output=_text(output))

    @_safe
    def on_tool_error(self, error, *, run_id, **kwargs):
        self._close(run_id, error=str(error)[:2000])
