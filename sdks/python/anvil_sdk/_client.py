"""Bounded queue + background flusher (ADR-0013).

The prime directive is that nothing in here can block or crash the host app.
Concretely that means: `submit()` never blocks and never raises, the queue is
bounded and sheds its *oldest* entries when full, all transport errors are
swallowed after being counted, and the flusher runs on a daemon thread so a
wedged Anvil can never hold up interpreter exit.
"""

from __future__ import annotations

import atexit
import logging
import random
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import httpx

from ._wire import Trace, apply_redact

log = logging.getLogger("anvil_sdk")

DEFAULT_BASE_URL = "http://localhost:8000"
# The wire schema caps a batch at 500 traces.
MAX_BATCH = 500
BACKOFF_BASE = 0.5
BACKOFF_MAX = 30.0


class Stats:
    """Counters a customer can read to see what the SDK is doing. Dropped
    traces are *counted*, not silent — ADR-0013 trades delivery for safety, so
    the trade has to be visible.

    Feedback keeps its own counters rather than sharing the trace ones. The two
    have wildly different volumes — thousands of traces to a handful of
    thumbs-downs — so a single `dropped` number would let trace loss hide
    feedback loss completely.
    """

    __slots__ = (
        "queued", "sent", "dropped", "failed_batches", "rejected",
        "feedback_queued", "feedback_sent", "feedback_dropped",
    )

    def __init__(self) -> None:
        self.queued = 0
        self.sent = 0
        self.dropped = 0
        self.failed_batches = 0
        self.rejected = 0
        self.feedback_queued = 0
        self.feedback_sent = 0
        self.feedback_dropped = 0

    def as_dict(self) -> dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}


class AnvilClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        flush_interval: float = 2.0,
        max_queue: int = 1000,
        redact: Optional[Callable[[Any], Any]] = None,
        timeout: float = 5.0,
        transport: Optional[httpx.BaseTransport] = None,
        start_worker: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.flush_interval = flush_interval
        self.redact = redact
        self.stats = Stats()

        # maxlen makes drop-oldest the deque's own behaviour: a full deque
        # discards from the left on append, which is exactly the policy.
        self._queue: deque = deque(maxlen=max_queue)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._backoff = 0.0
        # Traces handed to an in-flight request. Counted as still queued: during
        # an outage a customer polling stats() must never see "0 queued" while
        # their data is actually buffered and retrying.
        self._inflight = 0

        # Feedback rides the same flusher thread but its own queue and its own
        # backoff. Sharing the backoff would let a per-key *ingest* 429 — which
        # says nothing about the feedback endpoint's own limit — stall a user's
        # thumbs-down for thirty seconds.
        self._feedback: deque = deque(maxlen=max_queue)
        self._feedback_backoff = 0.0
        self._feedback_next_attempt = 0.0

        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "anvil-sdk-python",
            },
        )

        self._thread: Optional[threading.Thread] = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._run, name="anvil-flusher", daemon=True
            )
            self._thread.start()
            atexit.register(self.shutdown)

    # ---- producer side -------------------------------------------------

    def submit(self, trace: Trace) -> None:
        """Queue a finished trace. Never blocks, never raises."""
        try:
            if not apply_redact(trace, self.redact):
                return
            payload = trace.to_wire()
            with self._lock:
                was_full = len(self._queue) == self._queue.maxlen
                self._queue.append(payload)
                if was_full:
                    self.stats.dropped += 1
                self._sync_queued()
            self._wake.set()
        except Exception:  # noqa: BLE001 - the whole point of ADR-0013
            log.debug("anvil: dropped a trace during submit", exc_info=True)

    def feedback(
        self,
        trace_id: str,
        rating: int,
        comment: Optional[str] = None,
        user_ref: Optional[str] = None,
    ) -> None:
        """Attach an end-user thumbs up/down to a trace. Never blocks, never raises.

        `trace_id` is the id the SDK generated for the trace — round-trip it to
        your frontend (it is on `trace.id`, and on the callback handler's
        `handler.trace.id`) and send it back with the vote.

        `rating` is -1 for thumbs-down, +1 for thumbs-up. Anything else is
        dropped and logged: a `0` would file feedback that no filter surfaces
        and no failure inbox shows.

        Pass `user_ref` to make repeat votes an upsert — the same user toggling
        👎 → 👍 replaces their row rather than adding a contradictory second one.
        Without it, every call is its own anonymous row.

        Fire-and-forget, like `submit()`: the server answers 202 and persists
        asynchronously anyway, so there is nothing here worth blocking a user's
        request path for.
        """
        try:
            if rating not in (-1, 1):
                log.warning("anvil: feedback rating must be -1 or 1, got %r", rating)
                self.stats.feedback_dropped += 1
                return
            payload = {"trace_id": str(trace_id), "rating": int(rating)}
            if comment is not None:
                payload["comment"] = str(comment)
            if user_ref is not None:
                payload["user_ref"] = str(user_ref)
            with self._lock:
                was_full = len(self._feedback) == self._feedback.maxlen
                self._feedback.append(payload)
                if was_full:
                    self.stats.feedback_dropped += 1
                self.stats.feedback_queued = len(self._feedback)
            self._wake.set()
        except Exception:  # noqa: BLE001 - ADR-0013 applies here too
            log.debug("anvil: dropped feedback during submit", exc_info=True)

    # ---- consumer side -------------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            # Backoff extends the idle wait rather than sleeping separately, so
            # shutdown() can still interrupt it promptly.
            self._wake.wait(self.flush_interval + self._backoff)
            self._wake.clear()
            self._flush_once()
            self._flush_feedback()

    def _sync_queued(self) -> None:
        """Caller holds the lock."""
        self.stats.queued = len(self._queue) + self._inflight

    def _take_batch(self) -> list:
        with self._lock:
            batch = [self._queue.popleft() for _ in range(min(MAX_BATCH, len(self._queue)))]
            self._inflight = len(batch)
            self._sync_queued()
        return batch

    def _settle(self, count: int = 0) -> None:
        with self._lock:
            self._inflight = count
            self._sync_queued()

    def _requeue(self, batch: list) -> None:
        """Put a failed batch back at the front — it is the oldest data. If the
        queue filled while we were away there may not be room for all of it;
        drop-oldest still applies, so we keep the newest that fit and count the
        rest."""
        with self._lock:
            self._inflight = 0
            room = (self._queue.maxlen or len(batch)) - len(self._queue)
            keep = batch[-room:] if room > 0 else []
            self.stats.dropped += len(batch) - len(keep)
            self._queue.extendleft(reversed(keep))
            self._sync_queued()

    def _flush_once(self) -> None:
        batch = self._take_batch()
        if not batch:
            self._backoff = 0.0
            return
        try:
            response = self._http.post(
                f"{self.base_url}/v1/ingest/traces", json={"traces": batch}
            )
        except Exception:  # noqa: BLE001 - network down, DNS gone, TLS bad
            self.stats.failed_batches += 1
            self._requeue(batch)
            self._bump_backoff()
            log.debug("anvil: ingest request failed; will retry", exc_info=True)
            return

        if response.status_code < 300:
            self.stats.sent += len(batch)
            self._settle()
            self._backoff = 0.0
            return

        if response.status_code == 429 or response.status_code >= 500:
            # Retryable: rate limited, backpressured, or Anvil is unwell.
            self.stats.failed_batches += 1
            self._requeue(batch)
            self._bump_backoff(response.headers.get("Retry-After"))
            return

        # 400/401/413/422: retrying sends the same bad batch forever. Drop it,
        # loudly enough that a misconfigured key is findable in the host's logs.
        self.stats.rejected += len(batch)
        self._settle()
        self._backoff = 0.0
        log.warning(
            "anvil: ingest rejected %d traces with %s: %s",
            len(batch),
            response.status_code,
            response.text[:500],
        )

    # ---- feedback transport --------------------------------------------

    # Per flush cycle. The endpoint takes one submission per request, so this
    # caps how long a backlog can hold the flusher thread away from traces.
    FEEDBACK_PER_CYCLE = 20

    def _flush_feedback(self, max_items: int = FEEDBACK_PER_CYCLE) -> None:
        if time.monotonic() < self._feedback_next_attempt:
            return
        for _ in range(max_items):
            with self._lock:
                if not self._feedback:
                    self._feedback_backoff = 0.0
                    return
                item = self._feedback.popleft()
                self.stats.feedback_queued = len(self._feedback)
            if not self._send_feedback(item):
                return

    def _send_feedback(self, item: dict) -> bool:
        """Returns False if the caller should stop for this cycle."""
        try:
            response = self._http.post(f"{self.base_url}/v1/feedback", json=item)
        except Exception:  # noqa: BLE001 - network down, DNS gone, TLS bad
            self._requeue_feedback(item)
            self._bump_feedback_backoff()
            log.debug("anvil: feedback request failed; will retry", exc_info=True)
            return False

        if response.status_code < 300:
            self.stats.feedback_sent += 1
            self._feedback_backoff = 0.0
            self._feedback_next_attempt = 0.0
            return True

        if response.status_code == 429 or response.status_code >= 500:
            self._requeue_feedback(item)
            self._bump_feedback_backoff(response.headers.get("Retry-After"))
            return False

        # 400/401/404/422: the same submission will be refused forever.
        self.stats.feedback_dropped += 1
        log.warning(
            "anvil: feedback rejected with %s: %s",
            response.status_code,
            response.text[:500],
        )
        return True

    def _requeue_feedback(self, item: dict) -> None:
        with self._lock:
            if len(self._feedback) == self._feedback.maxlen:
                # The queue filled behind us. Drop-oldest still applies, but the
                # item in hand is the *oldest* of all, so it is the one to keep:
                # append it and let the deque shed its left end.
                self.stats.feedback_dropped += 1
            self._feedback.appendleft(item)
            self.stats.feedback_queued = len(self._feedback)

    def _bump_feedback_backoff(self, retry_after: Optional[str] = None) -> None:
        if retry_after:
            try:
                self._feedback_backoff = min(float(retry_after), BACKOFF_MAX)
                self._feedback_next_attempt = time.monotonic() + self._feedback_backoff
                return
            except (TypeError, ValueError):
                pass
        nxt = BACKOFF_BASE if self._feedback_backoff <= 0 else self._feedback_backoff * 2
        self._feedback_backoff = min(BACKOFF_MAX, nxt) * (0.5 + random.random() * 0.5)
        self._feedback_next_attempt = time.monotonic() + self._feedback_backoff

    def _bump_backoff(self, retry_after: Optional[str] = None) -> None:
        if retry_after:
            try:
                self._backoff = min(float(retry_after), BACKOFF_MAX)
                return
            except (TypeError, ValueError):
                pass
        nxt = BACKOFF_BASE if self._backoff <= 0 else self._backoff * 2
        # Full jitter: without it, every SDK instance that failed during an
        # outage retries in lockstep and re-DDoSes the API on recovery.
        self._backoff = min(BACKOFF_MAX, nxt) * (0.5 + random.random() * 0.5)

    # ---- lifecycle -----------------------------------------------------

    def flush(self, max_batches: int = 10) -> None:
        """Best-effort synchronous drain of traces *and* feedback."""
        for _ in range(max_batches):
            with self._lock:
                empty = not self._queue
            if empty:
                break
            self._flush_once()

        self._feedback_next_attempt = 0.0
        for _ in range(max_batches):
            with self._lock:
                empty = not self._feedback
            if empty:
                return
            self._flush_feedback()
            self._feedback_next_attempt = 0.0

    def shutdown(self) -> None:
        """Flush what we can on the way out. Best-effort by design: a hung
        Anvil must not add seconds to the host process's exit."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        self._wake.set()
        try:
            self._backoff = 0.0
            self._feedback_backoff = 0.0
            self.flush(max_batches=3)
        except Exception:  # noqa: BLE001
            log.debug("anvil: shutdown flush failed", exc_info=True)
        finally:
            try:
                self._http.close()
            except Exception:  # noqa: BLE001
                pass
