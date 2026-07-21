"""Trace/span shape: nesting, status, the decorator, and schema conformance."""

import asyncio

import pytest

import anvil_sdk as anvil
from anvil_sdk._wire import MAX_FIELD_BYTES, TRUNCATED_FLAG


def queued(client) -> list:
    return list(client._queue)


def test_nesting_parentage_and_schema(client, validate_batch):
    with anvil.trace("answer", input={"q": "why"}) as t:
        with anvil.span("retrieve", type="retrieval") as outer:
            outer.output = ["doc-1"]
            with anvil.span("embed", type="embedding") as inner:
                inner.output = [0.1, 0.2]
        with anvil.span("generate", type="llm") as gen:
            gen.end(output="because", tokens_in=10, tokens_out=4)
        t.output = "because"

    batch = queued(client)
    assert len(batch) == 1
    validate_batch(batch)

    spans = {s["name"]: s for s in batch[0]["spans"]}
    assert spans["retrieve"]["parent_span_id"] is None
    assert spans["embed"]["parent_span_id"] == spans["retrieve"]["id"]
    assert spans["generate"]["parent_span_id"] is None, "sibling, not nested under retrieve"
    assert spans["generate"]["tokens_in"] == 10
    assert batch[0]["status"] == "ok"
    assert all(s["status"] == "ok" for s in batch[0]["spans"])


def test_host_exception_marks_error_and_propagates(client, validate_batch):
    with pytest.raises(ValueError, match="boom"):
        with anvil.trace("failing") as t:
            with anvil.span("step", type="tool"):
                raise ValueError("boom")

    batch = queued(client)
    validate_batch(batch)
    assert batch[0]["status"] == "error"
    span = batch[0]["spans"][0]
    assert span["status"] == "error"
    assert "ValueError: boom" in span["error"]
    # The trace still ships despite the failure — errors are the traces you most
    # want to see.
    assert batch[0]["ended_at"] is not None


def test_observe_decorator_sync_and_async(client, validate_batch):
    @anvil.observe(type="tool")
    def lookup(city, unit="c"):
        return f"{city}:{unit}"

    @anvil.observe(type="llm", name="generate")
    async def generate(prompt):
        await asyncio.sleep(0)
        return "answer"

    with anvil.trace("mixed"):
        lookup("berlin")
        asyncio.run(generate("hi"))

    batch = queued(client)
    validate_batch(batch)
    spans = {s["name"]: s for s in batch[0]["spans"]}
    assert spans["lookup"]["input"] == {"city": "berlin", "unit": "c"}
    assert spans["lookup"]["output"] == "berlin:c"
    assert spans["generate"]["type"] == "llm"
    assert spans["generate"]["output"] == "answer"


def test_observe_outside_a_trace_still_produces_one(client, validate_batch):
    @anvil.observe(type="llm")
    def standalone(x):
        return x * 2

    assert standalone(21) == 42

    batch = queued(client)
    validate_batch(batch)
    assert batch[0]["name"] == "standalone"
    assert len(batch[0]["spans"]) == 1


def test_manual_api_round_trip(client, validate_batch):
    t = anvil.start_trace("manual", input="q")
    trace_id = t.id
    s = anvil.start_span("call", type="llm")
    s.end(output="a", tokens_out=3)
    t.end(output="a")

    batch = queued(client)
    validate_batch(batch)
    assert batch[0]["id"] == trace_id, "trace id is client-generated and stable"
    assert batch[0]["spans"][0]["tokens_out"] == 3


def test_ending_twice_is_a_no_op(client):
    t = anvil.start_trace("once")
    t.end(output="a")
    t.end(output="b")
    assert len(queued(client)) == 1


def test_nesting_survives_concurrent_tasks(client, validate_batch):
    """contextvars, not a shared stack: two tasks interleaving must not adopt
    each other's spans as parents."""

    async def work(label):
        with anvil.span(f"outer-{label}", type="chain"):
            await asyncio.sleep(0)
            with anvil.span(f"inner-{label}", type="tool"):
                await asyncio.sleep(0)

    async def main():
        with anvil.trace("concurrent"):
            await asyncio.gather(work("a"), work("b"))

    asyncio.run(main())

    batch = queued(client)
    validate_batch(batch)
    spans = {s["name"]: s for s in batch[0]["spans"]}
    assert spans["inner-a"]["parent_span_id"] == spans["outer-a"]["id"]
    assert spans["inner-b"]["parent_span_id"] == spans["outer-b"]["id"]
    assert spans["outer-a"]["parent_span_id"] is None
    assert spans["outer-b"]["parent_span_id"] is None


def test_oversized_fields_truncated_client_side(client, validate_batch):
    """Checklist row 6, client half: the SDK truncates before the wire so a
    chatty app degrades to previews instead of 413s."""
    huge = "x" * (MAX_FIELD_BYTES + 5000)

    with anvil.trace("big", input=huge) as t:
        with anvil.span("step", type="llm") as s:
            s.output = huge
        t.output = "small"

    batch = queued(client)
    validate_batch(batch)
    assert batch[0]["input"][TRUNCATED_FLAG] is True
    assert batch[0]["input"]["original_bytes"] > MAX_FIELD_BYTES
    assert len(batch[0]["input"]["preview"]) <= 2048
    assert batch[0]["spans"][0]["output"][TRUNCATED_FLAG] is True
    assert batch[0]["output"] == "small", "fields under the cap are untouched"


def test_unserializable_values_never_raise(client, validate_batch):
    class Exotic:
        def __repr__(self):
            return "<exotic>"

    with anvil.trace("weird", input=Exotic()) as t:
        with anvil.span("step", type="custom") as s:
            s.output = {"obj": Exotic(), "set": {1, 2}}
        t.output = Exotic()

    batch = queued(client)
    validate_batch(batch)
    assert batch[0]["input"] == "<exotic>"
    assert batch[0]["spans"][0]["output"]["obj"] == "<exotic>"


def test_span_cap_keeps_trace_valid(client, validate_batch):
    """The schema caps spans at 1000; a runaway loop must not get the whole
    trace 422'd."""
    with anvil.trace("runaway"):
        for i in range(1050):
            with anvil.span(f"s{i}", type="custom"):
                pass

    batch = queued(client)
    validate_batch(batch)
    assert len(batch[0]["spans"]) == 1000
