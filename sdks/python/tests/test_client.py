"""Transport behaviour under failure — this file is where ADR-0013 lives.

The rule under test throughout: whatever Anvil does, the host app is unaffected.
No blocking, no exceptions, bounded memory.
"""

import httpx
import respx

import anvil_sdk as anvil
from anvil_sdk._client import BACKOFF_MAX, AnvilClient

INGEST = "https://anvil.test/v1/ingest/traces"


def emit(name="t", n=1):
    for i in range(n):
        with anvil.trace(f"{name}-{i}"):
            pass


@respx.mock
def test_batches_and_sends(client):
    route = respx.post(INGEST).mock(return_value=httpx.Response(202, json={"accepted": 3}))
    emit(n=3)
    client.flush()

    assert route.call_count == 1, "one request for the whole batch, not one per trace"
    body = route.calls[0].request
    assert b'"traces"' in body.content
    assert body.headers["authorization"] == "Bearer ak_test"
    assert client.stats.sent == 3
    assert client.stats.queued == 0


@respx.mock
def test_full_queue_drops_oldest_and_counts(validate_batch):
    small = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", max_queue=5, start_worker=False
    )
    anvil.set_client(small)
    route = respx.post(INGEST).mock(return_value=httpx.Response(202))
    try:
        emit(name="trace", n=8)
        assert len(small._queue) == 5, "queue is bounded — memory can't run away"
        assert small.stats.dropped == 3

        small.flush()
        sent = route.calls[0].request.read()
        names = [t["name"] for t in __import__("json").loads(sent)["traces"]]
        # Oldest dropped, newest kept: recent traces are the ones you need when
        # you're debugging the incident that caused the backlog.
        assert names == ["trace-3", "trace-4", "trace-5", "trace-6", "trace-7"]
    finally:
        anvil.set_client(None)


@respx.mock
def test_api_down_never_raises_into_host_code(client):
    """The prime directive. Anvil is unreachable; the host app must not notice."""
    respx.post(INGEST).mock(side_effect=httpx.ConnectError("connection refused"))

    with anvil.trace("business-critical") as t:
        with anvil.span("work", type="tool") as s:
            s.output = "done"
        t.output = "done"
    client.flush()

    assert client.stats.failed_batches >= 1
    assert client.stats.sent == 0
    assert len(client._queue) == 1, "kept for retry, not lost on the first failure"


@respx.mock
def test_server_error_retries_then_succeeds(client):
    route = respx.post(INGEST).mock(
        side_effect=[httpx.Response(503), httpx.Response(202)]
    )
    emit(n=2)

    client._flush_once()
    assert client.stats.sent == 0
    assert client._backoff > 0, "backs off rather than hammering a sick API"
    assert len(client._queue) == 2, "batch requeued intact"

    client._flush_once()
    assert client.stats.sent == 2
    assert client._backoff == 0, "backoff resets after a success"
    assert route.call_count == 2


@respx.mock
def test_rate_limit_honours_retry_after(client):
    respx.post(INGEST).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5"}, json={})
    )
    emit(n=1)
    client._flush_once()

    assert client._backoff == 5.0
    assert len(client._queue) == 1


@respx.mock
def test_backoff_grows_and_is_capped(client):
    respx.post(INGEST).mock(return_value=httpx.Response(500))
    emit(n=1)

    seen = []
    for _ in range(12):
        client._flush_once()
        seen.append(client._backoff)

    assert seen[0] < seen[3], "exponential, not flat"
    assert all(b <= BACKOFF_MAX for b in seen), "capped so retries stay sane"
    assert len(set(seen)) > 1, "jittered — instances must not retry in lockstep"


@respx.mock
def test_queued_counts_in_flight_traces(client):
    """`queued` must never under-report while a batch is in flight.

    Found live: polling stats() during an outage intermittently showed 0 queued
    while six traces were buffered and retrying, because the count was taken
    after the batch left the deque and before the failure put it back. A
    customer diagnosing "is Anvil losing my data?" reads exactly this number.
    """
    observed = []

    def spy(request):
        observed.append(client.stats.queued)
        return httpx.Response(503)

    respx.post(INGEST).mock(side_effect=spy)
    emit(n=6)
    assert client.stats.queued == 6

    client._flush_once()
    assert observed == [6], "in-flight traces still count as queued"
    assert client.stats.queued == 6, "and still do once requeued"


@respx.mock
def test_unretryable_rejection_is_dropped_not_looped(client):
    """A 422 means the batch will never be accepted; retrying it forever would
    wedge the queue behind poison data."""
    route = respx.post(INGEST).mock(
        return_value=httpx.Response(422, json={"detail": {"code": "invalid_payload"}})
    )
    emit(n=2)

    client._flush_once()
    assert client.stats.rejected == 2
    assert len(client._queue) == 0

    client._flush_once()
    assert route.call_count == 1, "not resent"


@respx.mock
def test_requeue_respects_the_bound(validate_batch):
    """A failed batch coming back must not let the queue exceed max_queue."""
    small = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", max_queue=4, start_worker=False
    )
    anvil.set_client(small)
    respx.post(INGEST).mock(return_value=httpx.Response(503))
    try:
        emit(n=4)
        small._flush_once()
        assert len(small._queue) <= 4
        emit(n=4)
        assert len(small._queue) <= 4
        assert small.stats.dropped > 0
    finally:
        anvil.set_client(None)


@respx.mock
def test_shutdown_flushes_best_effort(validate_batch):
    instance = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", start_worker=False
    )
    anvil.set_client(instance)
    route = respx.post(INGEST).mock(return_value=httpx.Response(202))
    try:
        emit(n=2)
        instance.shutdown()
        assert route.call_count == 1
        assert instance.stats.sent == 2
    finally:
        anvil.set_client(None)


@respx.mock
def test_shutdown_with_a_dead_api_still_returns(validate_batch):
    """Exit must not hang on an unreachable Anvil."""
    instance = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", start_worker=False
    )
    anvil.set_client(instance)
    respx.post(INGEST).mock(side_effect=httpx.ConnectError("nope"))
    try:
        emit(n=2)
        instance.shutdown()  # must return, not raise or block
        assert instance.stats.sent == 0
    finally:
        anvil.set_client(None)


def test_no_client_configured_is_a_no_op():
    """Import and instrument before init() — a common ordering mistake that
    must not explode."""
    anvil.set_client(None)
    with anvil.trace("orphan") as t:
        with anvil.span("step", type="tool"):
            pass
        t.output = "fine"
    assert anvil.stats() == {}


def test_init_with_a_bad_url_disables_tracing_quietly():
    assert anvil.init(api_key="k", base_url="http://localhost:9", flush_interval=0.01)
    anvil.shutdown()


# ---- redact (checklist row 15) -----------------------------------------


@respx.mock
def test_redact_scrubs_before_anything_leaves_the_process(validate_batch):
    def scrub(obj):
        if getattr(obj, "input", None) and isinstance(obj.input, dict):
            obj.input = {**obj.input, "email": "[redacted]"}
        return obj

    instance = AnvilClient(
        api_key="ak_test",
        base_url="https://anvil.test",
        redact=scrub,
        start_worker=False,
    )
    anvil.set_client(instance)
    route = respx.post(INGEST).mock(return_value=httpx.Response(202))
    try:
        with anvil.trace("signup", input={"email": "a@b.com", "plan": "pro"}) as t:
            with anvil.span("verify", type="tool") as s:
                s.input = {"email": "a@b.com"}
            t.output = "ok"
        instance.flush()

        sent = route.calls[0].request.read().decode()
        assert "a@b.com" not in sent
        assert sent.count("[redacted]") == 2, "trace and span both scrubbed"
    finally:
        anvil.set_client(None)


def test_redact_returning_none_drops_the_trace(client):
    client.redact = lambda obj: None
    emit(n=1)
    assert len(client._queue) == 0


def test_redact_that_raises_drops_rather_than_leaks(client):
    """A buggy scrubber must not ship the data it failed to scrub — and must
    not crash the host either."""

    def broken(obj):
        raise RuntimeError("bad regex")

    client.redact = broken
    emit(n=1)
    assert len(client._queue) == 0
