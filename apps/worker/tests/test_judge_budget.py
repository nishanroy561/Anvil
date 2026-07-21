"""Judge timeout budget and judge cost accounting.

Both were found by running a real judged eval, not by the unit suite: 3 of 10
Gemini calls blew the 30 s deterministic-grader budget, and the run reported
$0.00 despite seven billable API calls.
"""

import asyncio

import pytest

from anvil_worker.config import get_settings
from anvil_worker.graders import GradeContext, load_graders, run_grader


@pytest.fixture(autouse=True)
def _registry():
    load_graders()


def test_judges_get_a_longer_budget_than_deterministic_graders():
    """A model round trip and a string comparison are not the same workload."""
    settings = get_settings()
    assert settings.judge_timeout_s > settings.grader_timeout_s


def test_a_slow_judge_survives_the_deterministic_budget(monkeypatch):
    """Regression: 30 s timed out ~30% of real judge calls."""
    settings = get_settings()
    monkeypatch.setattr(settings, "grader_timeout_s", 0.05, raising=False)
    monkeypatch.setattr(settings, "judge_timeout_s", 5, raising=False)

    class SlowJudge:
        """Slower than the deterministic budget, well inside the judge budget."""

        async def ainvoke(self, prompt):
            await asyncio.sleep(0.2)
            return {"score": 0.9, "reasoning": "took its time"}

    context = GradeContext(expected_output="x", model_factory=lambda _m: SlowJudge())
    result, error = asyncio.run(run_grader("accuracy", "x", context, {}))
    assert error is None, f"judge was cut off by the deterministic budget: {error}"
    assert result.score == 0.9


def test_a_judge_past_its_own_budget_still_times_out(monkeypatch):
    """The longer budget is a budget, not an absence of one."""
    settings = get_settings()
    monkeypatch.setattr(settings, "judge_timeout_s", 0.05, raising=False)

    class HangingJudge:
        async def ainvoke(self, prompt):
            await asyncio.sleep(30)

    context = GradeContext(expected_output="x", model_factory=lambda _m: HangingJudge())
    result, error = asyncio.run(run_grader("accuracy", "x", context, {}))
    assert result is None
    assert "timed out" in error


def test_every_builtin_judge_declares_its_kind():
    """`kind` is what earns the longer budget; a judge missing it gets 30 s."""
    from anvil_worker.graders.judges import BUILTINS

    for judge in BUILTINS:
        assert getattr(judge, "kind", None) == "judge", judge.name


def test_deterministic_graders_are_not_tagged_as_judges():
    from anvil_worker.graders.deterministic import BUILTINS

    for grader in BUILTINS:
        assert getattr(grader, "kind", "deterministic") != "judge", grader.name


# ---- cost accounting --------------------------------------------------------


class UsageReportingJudge:
    """Mimics the `include_raw=True` envelope a real provider returns."""

    def __init__(self, tokens_in=1000, tokens_out=200):
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out

    async def ainvoke(self, prompt):
        class Raw:
            usage_metadata = {"input_tokens": self.tokens_in, "output_tokens": self.tokens_out}

        return {"raw": Raw(), "parsed": {"score": 1.0, "reasoning": "ok"}, "parsing_error": None}


def test_judge_token_usage_is_captured_from_the_include_raw_envelope():
    context = GradeContext(
        expected_output="x", model_factory=lambda _m: UsageReportingJudge()
    )
    result, error = asyncio.run(
        run_grader("accuracy", "x", context, {"model": "gemini-2.5-flash"})
    )
    assert error is None
    assert result.tokens_in == 1000
    assert result.tokens_out == 200
    assert result.model == "gemini-2.5-flash"


def test_a_bare_verdict_reply_still_works_and_reports_no_usage():
    """Fake models in tests return a bare dict; that must not break."""
    class BareJudge:
        async def ainvoke(self, prompt):
            return {"score": 0.5, "reasoning": "meh"}

    context = GradeContext(expected_output="x", model_factory=lambda _m: BareJudge())
    result, error = asyncio.run(run_grader("accuracy", "x", context, {}))
    assert error is None
    assert result.score == 0.5
    assert result.tokens_in is None


def test_unparseable_structured_output_is_an_item_error_not_a_crash():
    class BrokenJudge:
        async def ainvoke(self, prompt):
            return {"raw": None, "parsed": None, "parsing_error": "not valid JSON"}

    context = GradeContext(expected_output="x", model_factory=lambda _m: BrokenJudge())
    result, error = asyncio.run(run_grader("accuracy", "x", context, {}))
    assert result is None
    assert "unparseable" in error


def test_judge_cost_lands_on_the_item_and_is_priced_per_grader():
    """1000 in / 200 out on gemini-2.5-flash = $0.30/M in + $2.50/M out."""
    from anvil_worker.eval_runner import _run_item

    run = {
        "id": "run",
        "target_config": {"mode": "rescore"},
        "grader_config": {"graders": [{"name": "accuracy", "config": {"model": "gemini-2.5-flash"}}]},
    }
    item = {"id": "item", "input": {"q": "x"}, "expected_output": "x", "metadata": None}

    row = asyncio.run(_run_item(run, item, lambda _m: UsageReportingJudge()))

    expected = (1000 / 1_000_000) * 0.30 + (200 / 1_000_000) * 2.50
    assert row["cost_usd"] == pytest.approx(expected)
    assert row["scores"]["accuracy"]["cost_usd"] == pytest.approx(expected)


def test_rescore_run_with_no_judges_costs_nothing():
    from anvil_worker.eval_runner import _run_item

    run = {
        "id": "run",
        "target_config": {"mode": "rescore"},
        "grader_config": {"graders": [{"name": "exact_match", "config": {}}]},
    }
    item = {"id": "item", "input": {"q": "x"}, "expected_output": "x", "metadata": None}
    row = asyncio.run(_run_item(run, item, None))
    assert row["cost_usd"] is None
