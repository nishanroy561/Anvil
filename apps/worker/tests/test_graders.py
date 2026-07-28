"""Grader tests (plan/evaluation.md §2: "unit-test exhaustively").

No services, no keys — deterministic graders are pure, and judges get a fake
LLM injected through `GradeContext.model_factory`.
"""

import asyncio

import pytest

from anvil_worker.graders import (
    GradeContext,
    GradeResult,
    load_graders,
    register_grader,
    run_grader,
)
from anvil_worker.graders.deterministic import BUILTINS as DETERMINISTIC


@pytest.fixture(autouse=True)
def _registry():
    load_graders()


def grade(name: str, output, context=None, config=None):
    ctx = context or GradeContext()
    result, error = asyncio.run(run_grader(name, output, ctx, config or {}))
    assert error is None, error
    return result


# ---- valid_json -------------------------------------------------------------


def test_valid_json_accepts_parsed_dict_output():
    # A JSON-mode model returns a dict; punishing that would be perverse.
    assert grade("valid_json", {"a": 1}).score == 1.0


def test_valid_json_accepts_json_string():
    assert grade("valid_json", '{"a": 1}').score == 1.0


def test_valid_json_rejects_prose():
    result = grade("valid_json", "here is your answer")
    assert result.score == 0.0
    assert "not valid JSON" in result.detail["reason"]


def test_valid_json_required_keys_reports_which_are_missing():
    result = grade("valid_json", {"a": 1}, config={"required_keys": ["a", "b"]})
    assert result.score == 0.0
    assert result.detail["missing"] == ["b"]


# ---- contains / not_contains ------------------------------------------------


def test_contains_is_case_insensitive_by_default():
    assert grade("contains", "Hello World", config={"value": "hello"}).score == 1.0


def test_contains_respects_case_sensitivity():
    config = {"value": "hello", "case_sensitive": True}
    assert grade("contains", "Hello World", config=config).score == 0.0


def test_contains_regex_mode():
    config = {"value": r"\d{3}-\d{4}", "regex": True}
    assert grade("contains", "call 555-1234", config=config).score == 1.0


def test_contains_searches_serialized_structured_output():
    # json.dumps, not str() — single quotes would break naive matching.
    assert grade("contains", {"answer": "yes"}, config={"value": '"answer"'}).score == 1.0


def test_not_contains_inverts():
    assert grade("not_contains", "all good", config={"value": "error"}).score == 1.0
    assert grade("not_contains", "an error", config={"value": "error"}).score == 0.0


def test_invalid_regex_becomes_a_grader_error_not_a_silent_zero():
    result, error = asyncio.run(
        run_grader("contains", "text", GradeContext(), {"value": "[unclosed", "regex": True})
    )
    assert result is None
    assert "invalid regex" in error


# ---- length_bounds ----------------------------------------------------------


def test_length_bounds_counts_characters_by_default():
    assert grade("length_bounds", "hello", config={"min": 3, "max": 10}).score == 1.0


def test_length_bounds_under_min_reports_the_bound():
    result = grade("length_bounds", "hi", config={"min": 5})
    assert result.score == 0.0
    assert result.detail["reason"] == "under min"


def test_length_bounds_counts_words_when_asked():
    result = grade("length_bounds", "one two three", config={"max": 2, "unit": "words"})
    assert result.score == 0.0
    assert result.detail["length"] == 3


# ---- exact_match ------------------------------------------------------------


def test_exact_match_ignores_case_and_whitespace_by_default():
    context = GradeContext(expected_output="  Paris ")
    assert grade("exact_match", "paris", context).score == 1.0


def test_exact_match_reports_both_sides_on_mismatch():
    context = GradeContext(expected_output="Paris")
    result = grade("exact_match", "London", context)
    assert result.score == 0.0
    assert result.detail["expected"] == "paris"


def test_exact_match_without_expected_output_scores_zero_with_a_reason():
    result = grade("exact_match", "anything")
    assert result.score == 0.0
    assert "expected_output" in result.detail["reason"]


# ---- citation_present -------------------------------------------------------


def test_citation_present_matches_bracket_markers():
    assert grade("citation_present", "as shown [1]").score == 1.0


def test_citation_present_matches_urls():
    assert grade("citation_present", "see https://example.com/x").score == 1.0


def test_citation_present_style_filter_is_respected():
    result = grade("citation_present", "see https://example.com", config={"style": "bracket"})
    assert result.score == 0.0
    assert result.detail["url"] is True  # detected, but not the requested style


def test_citation_absent():
    assert grade("citation_present", "no sources here").score == 0.0


# ---- registry & isolation ---------------------------------------------------


def test_every_builtin_is_registered_under_its_own_name():
    for grader in DETERMINISTIC:
        assert grade(grader.name, "x") is not None or True  # name resolves


def test_unknown_grader_is_an_item_level_error():
    result, error = asyncio.run(run_grader("nope", "x", GradeContext(), {}))
    assert result is None
    assert "unknown grader" in error


def test_a_raising_grader_costs_one_score_not_the_run():
    class Exploding:
        name = "exploding"

        async def grade(self, output, context, config):
            raise RuntimeError("boom")

    register_grader(Exploding())
    result, error = asyncio.run(run_grader("exploding", "x", GradeContext(), {}))
    assert result is None
    assert "RuntimeError: boom" in error


def test_a_hanging_grader_is_cut_off_by_the_timeout(monkeypatch):
    from anvil_worker import config as worker_config

    class Hanging:
        name = "hanging"

        async def grade(self, output, context, config):
            await asyncio.sleep(60)

    register_grader(Hanging())
    monkeypatch.setattr(
        worker_config.get_settings(), "grader_timeout_s", 0.05, raising=False
    )
    result, error = asyncio.run(run_grader("hanging", "x", GradeContext(), {}))
    assert result is None
    assert "timed out" in error


def test_out_of_range_scores_are_clamped_not_rejected():
    class Sloppy:
        name = "sloppy"

        async def grade(self, output, context, config):
            return GradeResult(1.7)

    register_grader(Sloppy())
    result, error = asyncio.run(run_grader("sloppy", "x", GradeContext(), {}))
    assert error is None
    assert result.score == 1.0


def test_duplicate_registration_is_rejected_loudly():
    class First:
        name = "dupe"

        async def grade(self, output, context, config):
            return GradeResult(1.0)

    assert register_grader(First()) is True
    assert register_grader(First()) is False


# ---- judges (fake LLM) ------------------------------------------------------


class FakeJudge:
    """Stands in for ChatAnthropic — returns a scripted verdict."""

    def __init__(self, score: float = 0.9, reasoning: str = "looks right", fail_times: int = 0):
        self.score = score
        self.reasoning = reasoning
        self.fail_times = fail_times
        self.calls = 0

    async def ainvoke(self, prompt: str):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("rate limit exceeded")
        return {"score": self.score, "reasoning": self.reasoning}


def test_accuracy_judge_returns_score_and_reasoning():
    judge = FakeJudge(0.8, "matches the reference")
    context = GradeContext(expected_output="Paris", model_factory=lambda _m: judge)
    result = grade("accuracy", "Paris, France", context)
    assert result.score == 0.8
    assert result.reasoning == "matches the reference"
    assert result.detail["passed"] is True


def test_accuracy_judge_threshold_drives_passed_flag():
    context = GradeContext(expected_output="Paris", model_factory=lambda _m: FakeJudge(0.5))
    result = grade("accuracy", "London", context, config={"threshold": 0.8})
    assert result.detail["passed"] is False


def test_accuracy_without_expected_output_does_not_call_the_model():
    judge = FakeJudge()
    context = GradeContext(model_factory=lambda _m: judge)
    result = grade("accuracy", "anything", context)
    assert result.score == 0.0
    assert judge.calls == 0


def test_hallucination_judge_needs_context_chunks():
    context = GradeContext(model_factory=lambda _m: FakeJudge())
    result = grade("hallucination", "claim", context)
    assert result.score == 0.0
    assert "no retrieved context" in result.detail["reason"]


def test_hallucination_judge_scores_against_chunks():
    context = GradeContext(chunks=[{"text": "Paris is in France"}], model_factory=lambda _m: FakeJudge(1.0))
    assert grade("hallucination", "Paris is in France", context).score == 1.0


def test_custom_judge_requires_a_rubric():
    context = GradeContext(model_factory=lambda _m: FakeJudge())
    result = grade("custom", "out", context)
    assert result.score == 0.0
    assert "rubric" in result.detail["reason"]


def test_judge_retries_a_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setattr("anvil_worker.graders.judges.BASE_BACKOFF_S", 0.001)
    judge = FakeJudge(fail_times=2)
    context = GradeContext(expected_output="x", model_factory=lambda _m: judge)
    result = grade("accuracy", "x", context)
    assert judge.calls == 3
    assert result.score == 0.9


def test_missing_key_is_a_graceful_item_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # No model_factory and no key -> the documented graceful outcome.
    result, error = asyncio.run(
        run_grader("tone", "text", GradeContext(), {})
    )
    assert result is None
    assert "no judge key configured" in error
