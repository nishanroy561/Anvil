"""Demo app tests — including the Phase 3 acceptance criterion that killing
Anvil does not break the host app (plan/sdk.md §3).

Keyless: the LLM is the mock chat model from `demo.chain` (CLAUDE.md).
"""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import anvil_sdk as anvil
from anvil_sdk._client import AnvilClient

from demo.app import app
from demo.chain import DEMO_MODELS, MockChatModel, build_chain
from demo.seed import seed

INGEST = "https://anvil.test/v1/ingest/traces"


@pytest.fixture
def collector():
    """A client that queues but never sends, so tests can read what the SDK
    would have shipped."""
    instance = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", start_worker=False
    )
    anvil.set_client(instance)
    yield instance
    anvil.set_client(None)


def test_ask_produces_a_correct_multi_span_trace(collector):
    with TestClient(app) as client:
        # The app's own lifespan called init(); point it back at the collector.
        anvil.set_client(collector)
        response = client.post("/ask", json={"question": "How long do refunds take?"})

    assert response.status_code == 200
    body = response.json()
    assert "5-7 business days" in body["answer"]
    assert body["sources"]
    assert body["trace_id"]

    trace = list(collector._queue)[-1]
    assert trace["id"] == body["trace_id"], "the id returned to the caller is the real one"
    assert trace["status"] == "ok"

    types = [s["type"] for s in trace["spans"]]
    assert "retrieval" in types and "llm" in types

    # Parent/child nesting is what the waterfall renders.
    ids = {s["id"] for s in trace["spans"]}
    roots = [s for s in trace["spans"] if s["parent_span_id"] is None]
    assert len(roots) == 1, "one root: the pipeline"
    assert all(s["parent_span_id"] in ids for s in trace["spans"] if s not in roots)

    llm = next(s for s in trace["spans"] if s["type"] == "llm")
    assert llm["tokens_in"] > 0 and llm["tokens_out"] > 0, "tokens drive cost"
    assert llm["attributes"]["model"] in DEMO_MODELS
    assert trace["model"] in DEMO_MODELS

    retrieval = next(s for s in trace["spans"] if s["type"] == "retrieval")
    assert len(retrieval["output"]) == 3
    assert retrieval["started_at"] <= llm["started_at"], "retrieval precedes generation"


def test_error_traces_are_still_captured(collector):
    chain = build_chain(model=MockChatModel(model="gpt-4o-mini", fail=True))
    from anvil_sdk.langchain import AnvilCallbackHandler

    handler = AnvilCallbackHandler(trace_name="ask")
    with pytest.raises(Exception, match="529"):
        chain.invoke("How long do refunds take?", config={"callbacks": [handler]})

    trace = list(collector._queue)[-1]
    assert trace["status"] == "error"
    assert "529" in trace["metadata"]["error"]
    assert any(s["type"] == "retrieval" for s in trace["spans"]), (
        "the spans that ran before the failure are kept"
    )


# ---- ADR-0013 acceptance ------------------------------------------------


def test_app_works_with_anvil_completely_unreachable():
    """Nothing is listening on this port. The demo app must not care."""
    with TestClient(app) as client:
        anvil.init(api_key="ak_x", base_url="http://127.0.0.1:9", flush_interval=0.05)
        for _ in range(5):
            response = client.post("/ask", json={"question": "Do you ship internationally?"})
            assert response.status_code == 200
            assert response.json()["answer"]
    anvil.shutdown()


@respx.mock
def test_anvil_dying_mid_run_does_not_break_the_app():
    """Anvil answers for a while, then goes away — the acceptance scenario."""
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] <= 1:
            return httpx.Response(202, json={"accepted": 1})
        raise httpx.ConnectError("anvil killed")

    respx.post(INGEST).mock(side_effect=flaky)
    instance = AnvilClient(
        api_key="ak_test", base_url="https://anvil.test", start_worker=False
    )
    anvil.set_client(instance)
    try:
        with TestClient(app) as client:
            anvil.set_client(instance)
            for i in range(8):
                response = client.post("/ask", json={"question": "What is the API rate limit?"})
                assert response.status_code == 200, f"request {i} failed after Anvil died"
                assert "300 requests per minute" in response.json()["answer"]
                instance.flush(max_batches=1)

        assert instance.stats.failed_batches >= 1, "the SDK did notice"
        assert len(instance._queue) <= 1000, "and stayed bounded"
    finally:
        anvil.set_client(None)


# ---- thumbs-down (Phase 9) ----------------------------------------------


def test_thumbs_down_posts_the_returned_trace_id():
    """The round trip the whole feedback loop rests on: `/ask` hands back a trace
    id, and the vote comes back carrying it."""
    # Assertions stay *inside* the respx block — it clears `.calls` on exit.
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://anvil.test/v1/feedback").mock(
            return_value=httpx.Response(202, json={"id": "queued"})
        )
        mock.post(INGEST).mock(return_value=httpx.Response(202, json={"accepted": 1}))
        with TestClient(app) as client:
            # Built *after* the app's lifespan ran `init()`, which shuts down
            # whichever client was installed — and shutdown closes its httpx
            # transport. A collector created beforehand can still queue but can
            # never send, which looks exactly like a broken endpoint.
            sdk = AnvilClient(
                api_key="ak_test", base_url="https://anvil.test", start_worker=False
            )
            anvil.set_client(sdk)
            asked = client.post("/ask", json={"question": "How long do refunds take?"})
            trace_id = asked.json()["trace_id"]

            voted = client.post(
                "/feedback",
                json={
                    "trace_id": trace_id,
                    "rating": -1,
                    "comment": "wrong, it took three weeks",
                    "user_ref": "user-9",
                },
            )

        assert voted.status_code == 200
        assert voted.json() == {"ok": True, "trace_id": trace_id, "rating": -1}
        assert route.call_count == 1
        assert json.loads(route.calls[0].request.read()) == {
            "trace_id": trace_id,
            "rating": -1,
            "comment": "wrong, it took three weeks",
            "user_ref": "user-9",
        }
    anvil.set_client(None)


def test_thumbs_down_does_not_break_when_anvil_is_unreachable():
    """A demo whose 👎 button 500s when Anvil is down would be advertising the
    opposite of ADR-0013."""
    with TestClient(app) as client:
        anvil.init(api_key="ak_x", base_url="http://127.0.0.1:9", flush_interval=0.05)
        response = client.post(
            "/feedback", json={"trace_id": "not-even-a-real-trace", "rating": -1}
        )
        assert response.status_code == 200
    anvil.shutdown()


def test_redact_hook_masks_emails_before_the_wire(collector):
    with TestClient(app) as client:
        anvil.set_client(collector)
        collector.redact = __import__("demo.app", fromlist=["_redact"])._redact
        response = client.post(
            "/ask", json={"question": "My email is user@example.com, where is my refund?"}
        )

    assert response.status_code == 200
    serialized = str(list(collector._queue)[-1])
    assert "user@example.com" not in serialized
    assert "[email redacted]" in serialized


# ---- seed generator -----------------------------------------------------


def test_seed_generates_varied_traffic(collector):
    counts = seed(n=40, verbose=False)
    traces = list(collector._queue)

    assert len(traces) == 40
    assert counts["error"] >= 1, "some errors, or the dashboard has nothing to show"
    assert {t["status"] for t in traces} == {"ok", "error"}
    assert len({t["model"] for t in traces}) > 1, "mixed models"
    assert len({t["session_id"] for t in traces}) > 1
    assert all(t["metadata"]["seeded"] is True for t in traces)
    assert {t["metadata"]["topic"] for t in traces}, "topics for metadata filtering"
