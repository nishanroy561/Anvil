"""Judge-call tracing into a designated internal project (plan/evaluation.md §2).

Anvil watching Anvil. Every LLM judge call is emitted as a trace through the
Anvil Python SDK, so a judged eval run becomes a waterfall in a dashboard like
any other instrumented app — the prompt that was sent, the verdict that came
back, tokens, latency, and errors. When a judge scores something strangely, the
prompt that produced it is a click away instead of a log grep.

Three rules, in the order they matter:

- **Off unless `ANVIL_INTERNAL_API_KEY` is set.** A self-hosted operator has no
  obligation to point their judge traffic anywhere, and the test suite must run
  keyless (CLAUDE.md). Unconfigured is the default and costs nothing.
- **Tracing can never affect a run.** Setup failures, a missing SDK, a dead API
  — every path here degrades to a no-op recorder. The SDK already refuses to
  raise into caller code (ADR-0013); this module holds that line for the setup
  it doesn't cover.
- **A dedicated client, not `anvil.init()`.** The global singleton would make
  any incidental `anvil.span()` anywhere in the worker ship to the internal
  project. The client is passed explicitly to the trace instead, so the only
  thing dogfooding captures is what this module wraps.

Point it at a **project created for this purpose**. Judge traces land through
the normal ingest path, so aiming it at a project under evaluation mixes
Anvil's own traffic into the customer data being measured.
"""

import logging
import os
from contextlib import ExitStack, contextmanager
from typing import Any, Iterator, Optional

from .providers import load_env

log = logging.getLogger(__name__)

API_KEY_ENV = "ANVIL_INTERNAL_API_KEY"
BASE_URL_ENV = "ANVIL_INTERNAL_BASE_URL"
DEFAULT_BASE_URL = "http://localhost:8000"

_client: Any = None
_resolved = False


def get_client() -> Optional[Any]:
    """The internal-project client, or None when dogfooding is off.

    Memoised: resolution reads the environment and opens a background flusher
    thread, and a judged run calls this once per item.
    """
    global _client, _resolved
    if _resolved:
        return _client
    _resolved = True

    # Same reasoning as providers.py: credentials come from the process
    # environment after an explicit repo-root load, never from settings
    # resolved against a working directory that differs between dev and image.
    load_env()
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        return None

    try:
        from anvil_sdk import AnvilClient
    except ImportError:
        # Deliberately a warning, not silence: the operator asked for this by
        # setting the key, so failing quietly would look like the feature is
        # broken rather than uninstalled.
        log.warning(
            "%s is set but anvil-sdk is not installed; judge tracing disabled "
            "(pip install -e sdks/python)",
            API_KEY_ENV,
        )
        return None

    base_url = os.getenv(BASE_URL_ENV) or DEFAULT_BASE_URL
    try:
        _client = AnvilClient(api_key=api_key, base_url=base_url)
        log.info("judge tracing enabled -> %s", base_url)
    except Exception:  # noqa: BLE001 - never break a run over telemetry
        log.warning("judge tracing setup failed; continuing untraced", exc_info=True)
        _client = None
    return _client


def reset() -> None:
    """Drops the memoised client, flushing what it holds. Shutdown and tests."""
    global _client, _resolved
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:  # noqa: BLE001
            log.debug("anvil: judge trace client shutdown failed", exc_info=True)
    _client = None
    _resolved = False


class Recorder:
    """Carries the judge's result onto the trace. A no-op when tracing is off,
    so callers never branch on whether dogfooding is enabled."""

    __slots__ = ("trace", "span")

    def __init__(self, trace: Any = None, span: Any = None) -> None:
        self.trace = trace
        self.span = span

    @property
    def enabled(self) -> bool:
        return self.span is not None

    def record(
        self,
        score: float,
        reasoning: str,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
    ) -> None:
        if self.span is None:
            return
        try:
            verdict = {"score": score, "reasoning": reasoning}
            self.span.tokens_in = tokens_in
            self.span.tokens_out = tokens_out
            self.span.output = verdict
            if self.trace is not None:
                self.trace.output = verdict
        except Exception:  # noqa: BLE001
            log.debug("anvil: recording judge verdict failed", exc_info=True)


_DISABLED = Recorder()


@contextmanager
def trace_judge(grader: str, model: Optional[str], prompt: str) -> Iterator[Recorder]:
    """Wraps one judge call as a trace: `judge:<grader>` with one `llm` span.

    The two-level shape is deliberate — tokens live on spans in the wire format,
    and it makes a judge trace look like any other instrumented LLM call rather
    than a special case in the waterfall.

    An exception from the wrapped block marks the trace errored and propagates:
    the judge's own retry/error handling stays in charge of what it means.
    """
    client = get_client()
    if client is None:
        yield _DISABLED
        return

    stack = ExitStack()
    try:
        import anvil_sdk as anvil

        traced = stack.enter_context(
            anvil.trace(
                f"judge:{grader}",
                input=prompt,
                model=model,
                metadata={"grader": grader, "component": "eval-runner"},
                client=client,
            )
        )
        spanned = stack.enter_context(anvil.span(model or "judge", type="llm", input=prompt))
        recorder = Recorder(traced, spanned)
    except Exception:  # noqa: BLE001
        log.debug("anvil: judge tracing setup failed", exc_info=True)
        try:
            stack.close()
        except Exception:  # noqa: BLE001
            pass
        yield _DISABLED
        return

    try:
        yield recorder
    except BaseException as exc:
        # Hand the exception to the SDK so the trace is marked errored, then let
        # it propagate untouched. ExitStack returns False here, never suppressing.
        try:
            stack.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:  # noqa: BLE001
            log.debug("anvil: judge trace teardown failed", exc_info=True)
        raise
    else:
        try:
            stack.close()
        except Exception:  # noqa: BLE001
            log.debug("anvil: judge trace teardown failed", exc_info=True)
