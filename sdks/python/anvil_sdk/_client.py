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
    the trade has to be visible."""

    __slots__ = ("queued", "sent", "dropped", "failed_batches", "rejected")

    def __init__(self) -> None:
        self.queued = 0
        self.sent = 0
        self.dropped = 0
        self.failed_batches = 0
        self.rejected = 0

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

    # ---- consumer side -------------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            # Backoff extends the idle wait rather than sleeping separately, so
            # shutdown() can still interrupt it promptly.
            self._wake.wait(self.flush_interval + self._backoff)
            self._wake.clear()
            self._flush_once()

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
        """Best-effort synchronous drain. Used by tests and by shutdown."""
        for _ in range(max_batches):
            with self._lock:
                empty = not self._queue
            if empty:
                return
            self._flush_once()

    def shutdown(self) -> None:
        """Flush what we can on the way out. Best-effort by design: a hung
        Anvil must not add seconds to the host process's exit."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        self._wake.set()
        try:
            self._backoff = 0.0
            self.flush(max_batches=3)
        except Exception:  # noqa: BLE001
            log.debug("anvil: shutdown flush failed", exc_info=True)
        finally:
            try:
                self._http.close()
            except Exception:  # noqa: BLE001
                pass
