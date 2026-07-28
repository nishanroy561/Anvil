"""Judge-call tracing (plan/evaluation.md §2). No services, no keys, no network.

The rule under test is the one that matters more than the feature: **telemetry
must never change a score or sink a run.** Every failure mode of the tracing
path — off, SDK missing, client construction raising, submit raising — has to
leave judging byte-identical to the untraced case.
"""

import pytest

from anvil_worker import dogfood
from anvil_worker.graders import GradeContext
from anvil_worker.graders.judges import AccuracyJudge, Verdict


class FakeClient:
    """Stands in for AnvilClient — the SDK only needs `submit` and `shutdown`."""

    def __init__(self, raise_on_submit: bool = False):
        self.traces = []
        self.raise_on_submit = raise_on_submit
        self.shutdown_calls = 0

    def submit(self, trace):
        if self.raise_on_submit:
            raise RuntimeError("api unreachable")
        self.traces.append(trace)

    def shutdown(self):
        self.shutdown_calls += 1


def fake_model(_name=None):
    """A judge model that returns a fixed verdict with usage attached."""

    class Model:
        async def ainvoke(self, _prompt):
            raw = type("Raw", (), {"usage_metadata": {"input_tokens": 11, "output_tokens": 7}})()
            return {"parsed": Verdict(score=0.5, reasoning="halfway"), "raw": raw}

    return Model()


def enable(monkeypatch, client):
    monkeypatch.setattr(dogfood, "_client", client)
    monkeypatch.setattr(dogfood, "_resolved", True)


async def grade(context=None):
    return await AccuracyJudge().grade(
        "an answer",
        context
        or GradeContext(input="q", expected_output="an answer", model_factory=fake_model),
        {"model": "claude-sonnet-5"},
    )


# ---- off by default --------------------------------------------------------


def test_no_key_means_no_client():
    """Unconfigured is the default: the suite and a bare checkout stay silent."""
    assert dogfood.get_client() is None


def test_disabled_recorder_is_inert():
    with dogfood.trace_judge("accuracy", "claude-sonnet-5", "prompt") as recorder:
        assert recorder.enabled is False
        recorder.record(1.0, "fine", 5, 5)  # must not raise


async def test_judging_is_unaffected_when_tracing_is_off():
    result = await grade()
    assert result.score == 0.5
    assert (result.tokens_in, result.tokens_out) == (11, 7)


# ---- enabled ---------------------------------------------------------------


async def test_judge_call_is_traced(monkeypatch):
    client = FakeClient()
    enable(monkeypatch, client)

    result = await grade()

    assert result.score == 0.5, "tracing must not disturb the score"
    assert len(client.traces) == 1
    traced = client.traces[0]
    assert traced.name == "judge:accuracy"
    assert traced.metadata["grader"] == "accuracy"
    assert traced.metadata["component"] == "eval-runner"
    assert traced.model == "claude-sonnet-5"
    assert traced.status == "ok"
    assert traced.output == {"score": 0.5, "reasoning": "halfway"}


async def test_the_traced_span_carries_the_model_and_its_tokens(monkeypatch):
    """Tokens live on spans in the wire format; a judge trace without them
    would show a cost of nothing."""
    client = FakeClient()
    enable(monkeypatch, client)

    await grade()

    (traced,) = client.traces
    assert len(traced.spans) == 1
    span = traced.spans[0]
    assert span.type == "llm"
    assert span.name == "claude-sonnet-5"
    assert (span.tokens_in, span.tokens_out) == (11, 7)
    assert span.output == {"score": 0.5, "reasoning": "halfway"}


async def test_the_prompt_is_captured(monkeypatch):
    """The point of the feature: the prompt behind a strange score is one click
    away rather than absent."""
    client = FakeClient()
    enable(monkeypatch, client)

    await grade()

    (traced,) = client.traces
    assert "Reference answer:" in traced.input
    assert "an answer" in traced.input


# ---- failure modes never reach the run -------------------------------------


async def test_a_dead_ingest_endpoint_does_not_break_judging(monkeypatch):
    """The SDK swallows submit failures; this pins that the judge still scores."""
    enable(monkeypatch, FakeClient(raise_on_submit=True))

    result = await grade()

    assert result.score == 0.5
    assert result.detail["passed"] is False


async def test_a_broken_sdk_does_not_break_judging(monkeypatch):
    """Setup raising must degrade to the no-op recorder, not propagate."""
    enable(monkeypatch, FakeClient())

    import anvil_sdk

    def boom(*_args, **_kwargs):
        raise RuntimeError("sdk is broken")

    monkeypatch.setattr(anvil_sdk, "trace", boom)

    result = await grade()
    assert result.score == 0.5


def test_client_construction_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv(dogfood.API_KEY_ENV, "ak_test")
    monkeypatch.setattr(dogfood, "_resolved", False)
    monkeypatch.setattr(dogfood, "load_env", lambda: None)

    import anvil_sdk

    def boom(*_args, **_kwargs):
        raise RuntimeError("bad base url")

    monkeypatch.setattr(anvil_sdk, "AnvilClient", boom)

    assert dogfood.get_client() is None


async def test_a_failing_judge_marks_the_trace_errored_and_still_raises(monkeypatch):
    """An error is the most useful thing to have traced, so it must be recorded
    — and the judge's own error handling must still see the exception."""
    client = FakeClient()
    enable(monkeypatch, client)

    def exploding_model(_name=None):
        class Model:
            async def ainvoke(self, _prompt):
                raise ValueError("provider said no")

        return Model()

    context = GradeContext(input="q", expected_output="a", model_factory=exploding_model)
    with pytest.raises(ValueError, match="provider said no"):
        await AccuracyJudge().grade("out", context, {"model": "claude-sonnet-5"})

    (traced,) = client.traces
    assert traced.status == "error"
    assert "provider said no" in traced.metadata["error"]
    assert traced.spans[0].status == "error"


# ---- lifecycle -------------------------------------------------------------


def test_reset_shuts_the_client_down_and_clears_memoisation(monkeypatch):
    client = FakeClient()
    enable(monkeypatch, client)

    dogfood.reset()

    assert client.shutdown_calls == 1
    assert dogfood._client is None
    assert dogfood._resolved is False


def test_reset_survives_a_client_that_fails_to_shut_down(monkeypatch):
    class Stubborn(FakeClient):
        def shutdown(self):
            raise RuntimeError("no")

    enable(monkeypatch, Stubborn())
    dogfood.reset()  # must not raise
    assert dogfood._client is None
