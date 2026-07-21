"""Trace/span objects and their serialization to the Anvil wire format.

The wire format is `packages/shared/trace-schema.json` and the ingest endpoint
validates it with `additionalProperties: false` — an extra key here is a 422 for
the customer, so `to_wire()` emits exactly the schema's fields and nothing else.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

SPAN_TYPES = ("llm", "retrieval", "tool", "embedding", "chain", "custom")

# Mirrors the server policy (plan/ingestion.md §1). Truncating here keeps batches
# well under the 5 MB body cap, so an app logging huge prompts degrades to
# previews instead of 413s (checklist row 6, client half).
MAX_FIELD_BYTES = 100_000
PREVIEW_CHARS = 2048

TRUNCATED_FLAG = "anvil_truncated"
ORIGINAL_BYTES = "original_bytes"


def new_id() -> str:
    """Client-generated UUIDs make ingestion idempotent — a retried batch
    upserts the same rows rather than duplicating them."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _json_default(value: Any) -> str:
    return repr(value)


def truncate_field(value: Any) -> Any:
    """Oversized field -> a preview marker matching the server's shape."""
    if value is None:
        return None
    try:
        encoded = json.dumps(value, default=_json_default)
    except (TypeError, ValueError):
        encoded = repr(value)
    size = len(encoded.encode("utf-8"))
    if size <= MAX_FIELD_BYTES:
        return value
    return {TRUNCATED_FLAG: True, ORIGINAL_BYTES: size, "preview": encoded[:PREVIEW_CHARS]}


def _coerce(value: Any) -> Any:
    """Make a value JSON-encodable without ever raising into host code.

    Customers pass whatever their app has — ORM objects, numpy arrays, Pydantic
    models. Losing fidelity on an exotic type is fine; raising is not.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        pass
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce(v) for v in value]
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return _coerce(method())
            except Exception:  # noqa: BLE001 - never break the host app
                break
    return repr(value)


@dataclass
class Span:
    name: str
    type: str = "custom"
    id: str = field(default_factory=new_id)
    parent_span_id: Optional[str] = None
    status: str = "in_progress"
    input: Any = None
    output: Any = None
    attributes: dict = field(default_factory=dict)
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    error: Optional[str] = None
    started_at: datetime = field(default_factory=utcnow)
    ended_at: Optional[datetime] = None

    # Set by the manual API (`start_span`) so `.end()` can close the contextvar
    # scope and submit. Unused by the context managers, which do it themselves.
    _on_end: Any = field(default=None, repr=False, compare=False)

    def end(
        self,
        output: Any = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        **attributes: Any,
    ) -> "Span":
        if output is not None:
            self.output = output
        if error is not None:
            self.error = error
        if tokens_in is not None:
            self.tokens_in = tokens_in
        if tokens_out is not None:
            self.tokens_out = tokens_out
        if attributes:
            self.attributes.update(attributes)
        self.status = status or ("error" if self.error else "ok")
        if self.ended_at is None:
            self.ended_at = utcnow()
        if self._on_end is not None:
            hook, self._on_end = self._on_end, None  # idempotent: end() twice is a no-op
            hook(self)
        return self

    def to_wire(self) -> dict:
        return {
            "id": self.id,
            "parent_span_id": self.parent_span_id,
            "name": str(self.name)[:512],
            "type": self.type if self.type in SPAN_TYPES else "custom",
            "status": self.status,
            "input": truncate_field(_coerce(self.input)),
            "output": truncate_field(_coerce(self.output)),
            "attributes": _coerce(self.attributes) or {},
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "error": self.error,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
        }


@dataclass
class Trace:
    name: str
    id: str = field(default_factory=new_id)
    status: str = "in_progress"
    input: Any = None
    output: Any = None
    metadata: dict = field(default_factory=dict)
    model: Optional[str] = None
    session_id: Optional[str] = None
    user_ref: Optional[str] = None
    started_at: datetime = field(default_factory=utcnow)
    ended_at: Optional[datetime] = None
    spans: list = field(default_factory=list)

    _on_end: Any = field(default=None, repr=False, compare=False)

    def end(
        self,
        output: Any = None,
        status: Optional[str] = None,
        **metadata: Any,
    ) -> "Trace":
        if output is not None:
            self.output = output
        if metadata:
            self.metadata.update(metadata)
        self.status = status or "ok"
        if self.ended_at is None:
            self.ended_at = utcnow()
        if self._on_end is not None:
            hook, self._on_end = self._on_end, None
            hook(self)
        return self

    def to_wire(self) -> dict:
        return {
            "id": self.id,
            "name": str(self.name)[:512],
            "status": self.status,
            "input": truncate_field(_coerce(self.input)),
            "output": truncate_field(_coerce(self.output)),
            "metadata": _coerce(self.metadata) or {},
            "model": self.model,
            "session_id": self.session_id,
            "user_ref": self.user_ref,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
            # Schema caps spans at 1000; drop the overflow rather than have the
            # whole trace rejected for it.
            "spans": [s.to_wire() for s in self.spans[:1000]],
        }


def apply_redact(trace: Trace, redact: Optional[Callable[[Any], Any]]) -> bool:
    """Runs the customer's redact hook over the trace and each span.

    Returns False if the hook asked to drop the trace (returned None). A hook
    that raises is ignored for that object — a buggy scrubber must not take
    down the host app, but it also must not silently ship the un-scrubbed data,
    so we drop the object instead.
    """
    if redact is None:
        return True
    try:
        result = redact(trace)
    except Exception:  # noqa: BLE001
        return False
    if result is None:
        return False
    kept = []
    for span in result.spans:
        try:
            scrubbed = redact(span)
        except Exception:  # noqa: BLE001
            continue
        if scrubbed is not None:
            kept.append(scrubbed)
    result.spans = kept
    return True
