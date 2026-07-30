"""`word_count_bounds` — scores 1.0 when the output's word count is in range.

A deliberately simple grader, chosen so the *contract* is the only thing this
file teaches. Real ones do more; none of them need to do anything structurally
different.

The contract (plan/plugins.md §1), all of it:

- a `name` attribute — the string operators pick in the UI and write in configs
- `kind` — `"deterministic"` (no model, tight timeout) or `"judge"` (gets a model
  through `context.model_factory`, longer timeout). Omit it and you are treated
  as deterministic.
- `compares_expected` — True only if you score the output *against*
  `context.expected_output`. Anvil uses this to flag a score as tautological when
  the two sides are the same value (ADR-0018). Omit it and you are treated as not
  comparing, so an unaware plugin is never falsely flagged.
- `async def grade(output, context, config) -> GradeResult`

`GradeResult` is duck-typed too: the worker reads `.score`, `.detail`,
`.reasoning`, `.tokens_in`, `.tokens_out` and `.model`. Returning your own small
class — as below — keeps the plugin free of an Anvil import.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    """Structurally what `anvil_worker.graders.GradeResult` is, without importing
    it. Anvil reads attributes, never the type."""

    score: float
    detail: dict = field(default_factory=dict)
    reasoning: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model: str | None = None


def _as_text(value: Any) -> str:
    """Outputs are JSON, so they are not always strings.

    A grader that assumed `str` would crash on every trace whose output is a
    dict — which, for a chat app returning `{"answer": …}`, is all of them.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("answer", "output", "text", "content", "message"):
            if isinstance(value.get(key), str):
                return value[key]
    return str(value)


class WordCountBounds:
    """1.0 when `min_words <= words <= max_words`, else 0.0.

    Why binary rather than a distance: Anvil aggregates every grader's score the
    same way, and a pass rate over "was it the right length" is a number an
    operator can act on. A graded distance would average into something nobody
    can set a threshold against.
    """

    name = "word_count_bounds"
    kind = "deterministic"
    # It judges the output on its own terms, so it is never tautological.
    compares_expected = False

    # Shown next to the name in the picker. Write it for the operator choosing a
    # grader, not for the developer who wrote it.
    description = "Scores 1.0 when the output's word count is within min/max."

    # Optional, and worth providing: the worker announces this to the API
    # (ADR-0023), which generates the config form in the eval-run and monitor
    # pages from it. A plain JSON Schema dict works; so does a pydantic model.
    # It drives the UI only — Anvil never uses it to reject a request, since it
    # arrived from this process and a bug here must not lock operators out of a
    # form. Validating your own config is still your job (see `grade`).
    config_schema = {
        "type": "object",
        "properties": {
            "min_words": {"type": "integer", "minimum": 0, "default": 0},
            "max_words": {"type": "integer", "minimum": 1, "default": 500},
        },
        "additionalProperties": False,
    }

    async def grade(self, output: Any, context: Any, config: dict) -> Result:
        text = _as_text(output)
        words = len(text.split())

        # Defaults that pass anything, so a misconfigured grader is visibly
        # useless rather than quietly failing every item. Anvil validates
        # *built-in* grader configs against a JSON Schema; plugin configs it
        # cannot see, so a plugin defends its own inputs.
        try:
            minimum = int(config.get("min_words", 0))
            maximum = int(config.get("max_words", 1_000_000))
        except (TypeError, ValueError):
            return Result(
                score=0.0,
                detail={"error": "min_words/max_words must be integers"},
            )

        if minimum > maximum:
            # Raising here would be honest too — Anvil records it as a grader
            # error and the run continues. Returning the reason is friendlier,
            # and puts it where the operator is already looking.
            return Result(
                score=0.0,
                detail={"error": f"min_words {minimum} is above max_words {maximum}"},
            )

        ok = minimum <= words <= maximum
        return Result(
            score=1.0 if ok else 0.0,
            detail={"words": words, "min_words": minimum, "max_words": maximum},
            reasoning=(
                f"{words} words, within {minimum}-{maximum}"
                if ok
                else f"{words} words, outside {minimum}-{maximum}"
            ),
        )
