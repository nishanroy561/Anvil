"""`client.feedback()` — Phase 9's SDK surface (plan/evaluation.md §7).

ADR-0013 applies here exactly as it does to traces: a thumbs-down is collected on
a user's request path, so nothing in this file may block or raise. What is
*different* from traces is volume — a handful of votes against thousands of
traces — which is why feedback carries its own queue, its own backoff and its own
counters. A shared `dropped` number would let trace loss hide feedback loss.
"""

import json

import httpx
import respx

import anvil_sdk as anvil
from anvil_sdk._client import AnvilClient

FEEDBACK = "https://anvil.test/v1/feedback"
INGEST = "https://anvil.test/v1/ingest/traces"

TRACE_ID = "3f1c2a5e-0000-4000-8000-000000000001"


def sent_bodies(route) -> list[dict]:
    return [json.loads(call.request.read()) for call in route.calls]


@respx.mock
def test_feedback_is_posted_with_the_key(client):
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202, json={"id": "x"}))
    client.feedback(TRACE_ID, -1, comment="wrong date", user_ref="user-7")
    client.flush()

    assert route.call_count == 1
    assert sent_bodies(route)[0] == {
        "trace_id": TRACE_ID,
        "rating": -1,
        "comment": "wrong date",
        "user_ref": "user-7",
    }
    assert route.calls[0].request.headers["authorization"] == "Bearer ak_test"
    assert client.stats.feedback_sent == 1
    assert client.stats.feedback_queued == 0


@respx.mock
def test_optional_fields_are_omitted_not_nulled(client):
    """The endpoint forbids unknown keys and validates the ones it gets; sending
    explicit nulls is a needless way to depend on server-side coercion."""
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))
    client.feedback(TRACE_ID, 1)
    client.flush()
    assert sent_bodies(route)[0] == {"trace_id": TRACE_ID, "rating": 1}


@respx.mock
def test_module_level_helper_uses_the_global_client(client):
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))
    anvil.feedback(TRACE_ID, -1)
    client.flush()
    assert route.call_count == 1


def test_module_level_helper_without_init_is_a_no_op():
    """A host that never called `init()` must not crash on a thumbs-down."""
    anvil.set_client(None)
    anvil.feedback(TRACE_ID, -1)  # no exception


@respx.mock
def test_invalid_rating_is_dropped_not_raised(client):
    """A `0` would file feedback that no filter surfaces and no failure inbox
    shows — but refusing it must not throw into the caller either."""
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))
    for rating in (0, 2, -3, None):
        client.feedback(TRACE_ID, rating)
    client.flush()

    assert route.call_count == 0
    assert client.stats.feedback_dropped == 4


@respx.mock
def test_api_down_never_raises_and_keeps_the_vote(client):
    respx.post(FEEDBACK).mock(side_effect=httpx.ConnectError("connection refused"))
    client.feedback(TRACE_ID, -1, comment="keep me")
    client.flush()

    assert client.stats.feedback_sent == 0
    assert client.stats.feedback_dropped == 0
    assert client.stats.feedback_queued == 1, "held for retry, not lost"
    assert client._feedback[0]["comment"] == "keep me"


@respx.mock
def test_server_error_retries_then_succeeds(client):
    route = respx.post(FEEDBACK).mock(
        side_effect=[httpx.Response(503), httpx.Response(202)]
    )
    client.feedback(TRACE_ID, -1)
    client.flush()

    assert route.call_count == 2
    assert client.stats.feedback_sent == 1
    assert client.stats.feedback_queued == 0


@respx.mock
def test_rate_limited_backs_off_and_retries(client):
    route = respx.post(FEEDBACK).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(202),
        ]
    )
    client.feedback(TRACE_ID, -1)
    client._flush_feedback()
    assert client.stats.feedback_sent == 0
    assert client._feedback_backoff == 2.0

    # flush() clears the deadline explicitly — that is what makes shutdown able
    # to drain a queue that is mid-backoff.
    client.flush()
    assert route.call_count == 2
    assert client.stats.feedback_sent == 1


@respx.mock
def test_rejection_is_dropped_rather_than_looped(client):
    """A 422 will be refused identically forever; retrying it is a spin."""
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(422, text="bad rating"))
    client.feedback(TRACE_ID, -1)
    client.flush()

    assert route.call_count == 1
    assert client.stats.feedback_dropped == 1
    assert client.stats.feedback_queued == 0


@respx.mock
def test_feedback_backoff_is_independent_of_ingest(client):
    """A per-key *ingest* 429 says nothing about the feedback endpoint's own
    limit; sharing one backoff would stall a user's thumbs-down for it."""
    respx.post(INGEST).mock(return_value=httpx.Response(429))
    feedback_route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))

    with anvil.trace("t"):
        pass
    client.feedback(TRACE_ID, -1)

    client._flush_once()
    assert client._backoff > 0
    client._flush_feedback()

    assert feedback_route.call_count == 1
    assert client.stats.feedback_sent == 1


@respx.mock
def test_queue_is_bounded_and_keeps_the_oldest_votes(validate_batch):
    """Drop-oldest is right for traces (you want the newest when debugging) but
    feedback is queued in submission order and drained FIFO, so a full queue
    sheds the oldest for the same reason: bounded memory, counted loss."""
    small = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", max_queue=3, start_worker=False
    )
    anvil.set_client(small)
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))
    try:
        for i in range(6):
            small.feedback(TRACE_ID, -1, comment=f"c{i}")
        assert len(small._feedback) == 3
        assert small.stats.feedback_dropped == 3

        small.flush()
        assert [b["comment"] for b in sent_bodies(route)] == ["c3", "c4", "c5"]
    finally:
        anvil.set_client(None)


@respx.mock
def test_one_cycle_drains_at_most_its_cap(client):
    """The endpoint takes one submission per request, so an unbounded drain would
    let a feedback backlog monopolise the flusher thread that traces need."""
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))
    for i in range(25):
        client.feedback(TRACE_ID, -1, comment=f"c{i}")

    client._flush_feedback()
    assert route.call_count == AnvilClient.FEEDBACK_PER_CYCLE
    assert client.stats.feedback_queued == 25 - AnvilClient.FEEDBACK_PER_CYCLE


@respx.mock
def test_flush_drains_traces_and_feedback_together(client):
    """`anvil.flush()` is what a demo script and a shutdown hook both call; a
    version that drained only traces would silently lose the vote."""
    ingest = respx.post(INGEST).mock(return_value=httpx.Response(202))
    feedback = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))

    with anvil.trace("t"):
        pass
    client.feedback(TRACE_ID, -1)
    anvil.flush()

    assert ingest.call_count == 1
    assert feedback.call_count == 1


@respx.mock
def test_shutdown_ships_a_queued_vote(client):
    route = respx.post(FEEDBACK).mock(return_value=httpx.Response(202))
    client.feedback(TRACE_ID, 1)
    client.shutdown()
    assert route.call_count == 1
    assert client.stats.feedback_sent == 1


@respx.mock
def test_shutdown_with_a_dead_api_still_exits(client):
    """Best-effort by design: a hung Anvil must not add seconds to process exit."""
    respx.post(FEEDBACK).mock(side_effect=httpx.ConnectError("down"))
    client.feedback(TRACE_ID, -1)
    client.shutdown()  # returns rather than hanging or raising


def test_stats_reports_feedback_separately(client):
    stats = client.stats.as_dict()
    for key in ("feedback_queued", "feedback_sent", "feedback_dropped"):
        assert key in stats
    # And the trace counters are untouched, so an existing dashboard reading
    # `dropped` doesn't start seeing feedback in it.
    assert set(stats) >= {"queued", "sent", "dropped", "failed_batches", "rejected"}
