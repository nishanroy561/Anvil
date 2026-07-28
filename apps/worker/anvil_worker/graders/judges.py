"""LLM-as-judge graders (plan/evaluation.md §2).

Judges are LangChain chat calls with structured output, so a judge returns
`{score: 0..1, reasoning: str}` rather than prose we'd have to parse. The
provider comes from the configured model name (ADR-0016): `claude-sonnet-5` is
the documented default, `gemini-*` also works.

Three rules shape everything here:

- **No key configured is a graceful outcome, not a crash.** The suite must run
  keyless (CLAUDE.md), so a missing key yields the item-level error
  "no judge key configured" and the run continues.
- **Provider 429s are retried with jittered backoff** (checklist row 10); after
  the last attempt the item records an error and the run continues.

- **Judge calls are themselves traced** into a designated internal project
  (evaluation.md §2's dogfooding), off unless `ANVIL_INTERNAL_API_KEY` is set.
  See `dogfood.py`; tracing never affects a score.
"""

import asyncio
import logging
import random
from typing import Any

from pydantic import BaseModel, Field

from ..dogfood import trace_judge
from ..providers import NO_KEY_ERROR, build_chat_model, default_provider
from . import GradeContext, GradeResult

log = logging.getLogger(__name__)

__all__ = ["NO_KEY_ERROR", "Verdict", "BUILTINS"]

MAX_ATTEMPTS = 4
BASE_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0


class Verdict(BaseModel):
    """The judge's structured reply — the only shape a judge may return."""

    score: float = Field(ge=0.0, le=1.0, description="0 is worst, 1 is best.")
    reasoning: str = Field(description="One or two sentences justifying the score.")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json

    return json.dumps(value, ensure_ascii=False)[:8000]


def _is_retryable(exc: Exception) -> bool:
    """429s and overloads are worth retrying; a bad request is not."""
    status = getattr(exc, "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504, 529):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(s in text for s in ("rate limit", "overloaded", "timeout", "too many requests"))


def _build_model(config: dict, model_factory: Any):
    """Returns a structured-output model handle, or raises RuntimeError if no key.

    `model_factory` lets the caller (and tests) inject a fake LLM; when absent
    the provider is resolved from the configured model name, falling back to
    whichever key is present (ADR-0016).

    `include_raw=True` is load-bearing: the plain structured-output wrapper
    returns only the parsed object and discards the response message, taking
    token usage with it — and a judge whose spend is invisible defeats the
    point of an observability tool reporting run cost.
    """
    model_name = config.get("model")

    if model_factory is not None:
        return model_factory(model_name)

    # Structured output is what makes a judge a judge: the reply is a Verdict,
    # not prose we'd have to parse and guess at.
    return build_chat_model(model_name).with_structured_output(Verdict, include_raw=True)


def _unpack(reply: Any) -> tuple[Any, int | None, int | None]:
    """Splits a judge reply into (verdict, tokens_in, tokens_out).

    Handles all three shapes we see: the `include_raw` envelope from a real
    provider, and the bare Verdict or dict a fake model returns in tests.
    """
    if isinstance(reply, dict) and "parsed" in reply:
        raw = reply.get("raw")
        usage = getattr(raw, "usage_metadata", None) or {}
        parsed = reply["parsed"]
        if parsed is None:
            error = reply.get("parsing_error")
            raise RuntimeError(f"judge returned unparseable output: {error}")
        return parsed, usage.get("input_tokens"), usage.get("output_tokens")
    return reply, None, None


async def _judge(grader: str, prompt: str, config: dict, context: GradeContext) -> GradeResult:
    """One judged call with retry. Raises RuntimeError for unrecoverable states.

    `grader` names the trace when judge tracing is on (dogfood.py); it has no
    effect on scoring.
    """
    model = _build_model(config, context.model_factory)
    model_name = config.get("model") or (default_provider() or (None, None))[1]

    # The whole call including retries is one trace: a judge that succeeded on
    # its third attempt is exactly the thing worth being able to see.
    with trace_judge(grader, model_name, prompt) as recorder:
        last_exc: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                verdict = await model.ainvoke(prompt)
                break
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                    raise
                # Jittered, capped exponential backoff (checklist row 10).
                delay = min(BASE_BACKOFF_S * (2**attempt), MAX_BACKOFF_S)
                await asyncio.sleep(delay * (0.5 + random.random() / 2))
        else:  # pragma: no cover - loop always breaks or raises
            raise last_exc or RuntimeError("judge failed")

        verdict, tokens_in, tokens_out = _unpack(verdict)

        # Structured output may arrive as a Verdict or a plain dict depending on
        # the LangChain version and whether a fake model was injected.
        if isinstance(verdict, Verdict):
            score, reasoning = verdict.score, verdict.reasoning
        elif isinstance(verdict, dict):
            score = float(verdict.get("score", 0.0))
            reasoning = str(verdict.get("reasoning", ""))
        else:
            score = float(getattr(verdict, "score", 0.0))
            reasoning = str(getattr(verdict, "reasoning", ""))

        recorder.record(score, reasoning, tokens_in, tokens_out)

    threshold = float(config.get("threshold", 0.7))
    return GradeResult(
        score=score,
        detail={"threshold": threshold, "passed": score >= threshold},
        reasoning=reasoning,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=model_name,
    )


class AccuracyJudge:
    name = "accuracy"
    kind = "judge"
    compares_expected = True

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        if context.expected_output is None:
            return GradeResult(0.0, {"reason": "item has no expected_output to judge against"})
        prompt = (
            "You are grading an AI system's answer against a reference answer.\n\n"
            f"Question / input:\n{_text(context.input)}\n\n"
            f"Reference answer:\n{_text(context.expected_output)}\n\n"
            f"Answer to grade:\n{_text(output)}\n\n"
            "Score 1.0 if the answer conveys the same substance as the reference, "
            "0.0 if it contradicts it or misses the point, and a value in between "
            "for partial matches. Wording may differ; judge meaning, not phrasing."
        )
        return await _judge(self.name, prompt, config, context)


class HallucinationJudge:
    name = "hallucination"
    kind = "judge"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        chunks = context.chunks or []
        if not chunks:
            return GradeResult(0.0, {"reason": "no retrieved context supplied to judge against"})
        prompt = (
            "You are checking whether an AI answer is grounded in the provided context.\n\n"
            f"Context:\n{_text(chunks)}\n\n"
            f"Answer:\n{_text(output)}\n\n"
            "Score 1.0 if every claim in the answer is supported by the context, "
            "0.0 if the answer asserts things the context does not support, and a "
            "value in between when only some claims are grounded. Do not penalize "
            "the answer for omitting context."
        )
        return await _judge(self.name, prompt, config, context)


class ToneJudge:
    name = "tone"
    kind = "judge"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        desired = config.get("tone", "professional and helpful")
        prompt = (
            f"You are judging whether a piece of text matches a requested tone.\n\n"
            f"Requested tone: {desired}\n\n"
            f"Text:\n{_text(output)}\n\n"
            "Score 1.0 if the text fully matches the requested tone, 0.0 if it "
            "clearly does not, and a value in between for a partial match. Judge "
            "tone only — not factual accuracy."
        )
        return await _judge(self.name, prompt, config, context)


class CustomJudge:
    name = "custom"
    kind = "judge"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        rubric = (config.get("rubric") or "").strip()
        if not rubric:
            return GradeResult(0.0, {"reason": "custom judge requires a rubric"})
        prompt = (
            "You are grading an AI system's output against a rubric.\n\n"
            f"Rubric:\n{rubric}\n\n"
            f"Input:\n{_text(context.input)}\n\n"
            f"Output to grade:\n{_text(output)}\n\n"
            "Score 1.0 if the output fully satisfies the rubric, 0.0 if it fails "
            "it entirely, and a value in between for partial satisfaction."
        )
        return await _judge(self.name, prompt, config, context)


BUILTINS = (AccuracyJudge(), HallucinationJudge(), ToneJudge(), CustomJudge())
