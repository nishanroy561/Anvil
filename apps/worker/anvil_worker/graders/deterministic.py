"""Deterministic graders (plan/evaluation.md §2).

No model calls, no network, no cost — these are the graders you can afford to
run on every item of every run. Each returns 0.0/1.0 plus a `detail` dict
explaining *why*, because "score 0" with no reason is not debuggable.

Outputs arrive as whatever the target produced: a string, a dict from a JSON
mode model, or a list. `_as_text` is the single place that decision is made, so
every grader treats a given output the same way.
"""

import json
import re
from typing import Any

from . import GradeContext, GradeResult


def _as_text(value: Any) -> str:
    """Renders any output as the text a grader should match against.

    Dicts and lists are serialized rather than str()'d so `contains` behaves
    predictably on structured output (single quotes would break naive matches).
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class ValidJson:
    name = "valid_json"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        required = config.get("required_keys") or []

        # A dict output is already parsed JSON — don't punish a model that was
        # asked for JSON mode and complied.
        if isinstance(output, (dict, list)):
            parsed = output
        else:
            try:
                parsed = json.loads(_as_text(output))
            except (json.JSONDecodeError, TypeError) as exc:
                return GradeResult(0.0, {"reason": "not valid JSON", "error": str(exc)[:200]})

        if required:
            if not isinstance(parsed, dict):
                return GradeResult(
                    0.0, {"reason": "required_keys set but output is not a JSON object"}
                )
            missing = [k for k in required if k not in parsed]
            if missing:
                return GradeResult(0.0, {"reason": "missing keys", "missing": missing})

        return GradeResult(1.0, {"reason": "parsed"})


def _matches(text: str, config: dict) -> tuple[bool, dict]:
    value = config.get("value", "")
    if config.get("regex"):
        flags = 0 if config.get("case_sensitive") else re.IGNORECASE
        try:
            hit = re.search(value, text, flags) is not None
        except re.error as exc:
            # A bad pattern is the config's fault; surface it as a grader error
            # rather than silently scoring 0.
            raise ValueError(f"invalid regex {value!r}: {exc}") from exc
        return hit, {"pattern": value, "regex": True}

    if config.get("case_sensitive"):
        return value in text, {"needle": value, "regex": False}
    return value.lower() in text.lower(), {"needle": value, "regex": False}


class Contains:
    name = "contains"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        hit, detail = _matches(_as_text(output), config)
        return GradeResult(1.0 if hit else 0.0, {**detail, "found": hit})


class NotContains:
    name = "not_contains"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        hit, detail = _matches(_as_text(output), config)
        return GradeResult(0.0 if hit else 1.0, {**detail, "found": hit})


class LengthBounds:
    name = "length_bounds"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        text = _as_text(output)
        unit = config.get("unit", "chars")
        length = len(text.split()) if unit == "words" else len(text)

        low = config.get("min")
        high = config.get("max")
        if low is not None and length < low:
            return GradeResult(0.0, {"length": length, "unit": unit, "reason": "under min", "min": low})
        if high is not None and length > high:
            return GradeResult(0.0, {"length": length, "unit": unit, "reason": "over max", "max": high})
        return GradeResult(1.0, {"length": length, "unit": unit})


class ExactMatch:
    name = "exact_match"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        if context.expected_output is None:
            return GradeResult(0.0, {"reason": "item has no expected_output to compare against"})

        actual = _as_text(output)
        expected = _as_text(context.expected_output)
        if config.get("strip", True):
            actual, expected = actual.strip(), expected.strip()
        if not config.get("case_sensitive", False):
            actual, expected = actual.lower(), expected.lower()

        hit = actual == expected
        return GradeResult(
            1.0 if hit else 0.0,
            {"matched": hit} if hit else {"matched": False, "expected": expected[:200], "actual": actual[:200]},
        )


# `[1]` / `[12]` style markers, or a bare http(s) URL.
BRACKET_CITATION = re.compile(r"\[\d+\]")
URL_CITATION = re.compile(r"https?://\S+")


class CitationPresent:
    name = "citation_present"

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult:
        text = _as_text(output)
        style = config.get("style", "either")

        bracket = BRACKET_CITATION.search(text) is not None
        url = URL_CITATION.search(text) is not None
        hit = {"bracket": bracket, "url": url, "either": bracket or url}[style]

        return GradeResult(
            1.0 if hit else 0.0, {"style": style, "bracket": bracket, "url": url}
        )


BUILTINS = (
    ValidJson(),
    Contains(),
    NotContains(),
    LengthBounds(),
    ExactMatch(),
    CitationPresent(),
)
