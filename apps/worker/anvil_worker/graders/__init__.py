"""Grader contract and registry (plan/plugins.md §1, plan/evaluation.md §2).

Built-ins are just plugins that ship in-tree — they register through the same
contract external packages use, which is what keeps the contract honest.

Two invariants, both from plugins.md §3:
- **A grader can never take down a run.** Every call is wrapped with a timeout
  and try/except; a raising or hanging grader yields an item-level grader error,
  which the aggregation rule already handles (excluded from the mean, counted in
  `errored_items`).
- **Cloud mode loads built-ins only** (ADR-0012) — third-party Python does not
  execute in a deployment hosting other people's data.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import get_settings

log = logging.getLogger(__name__)

GRADERS_ENTRY_POINT = "anvil.graders"
CATALOG_PATH = Path(__file__).resolve().parent.parent / "graders_catalog.json"


@dataclass
class GradeResult:
    """A grader's verdict on one item.

    `score` is always 0..1 — binary graders use 0.0/1.0 so deterministic and
    judge scores aggregate the same way.

    `tokens_in`/`tokens_out` are populated by LLM-backed graders so the run's
    cost reflects what judging actually spent; deterministic graders leave them
    None and cost nothing.
    """

    score: float
    detail: dict = field(default_factory=dict)
    reasoning: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model: str | None = None


@dataclass
class GradeContext:
    """What graders get besides the output (plan/plugins.md §1).

    `model_factory` hands LLM-backed graders a configured LangChain model so
    plugins never manage their own credentials.
    """

    input: Any = None
    expected_output: Any = None
    chunks: list | None = None
    metadata: dict | None = None
    model_factory: Any = None


class Grader(Protocol):
    name: str
    # "deterministic" (the default) or "judge". Only affects the timeout budget
    # a grader gets; plugins that omit it are treated as deterministic.
    kind: str
    # True when the grader scores the output *against* `context.expected_output`
    # rather than judging it on its own terms. Such a grader is meaningless when
    # the two are the same value, which is exactly what a rescore run over
    # unedited promoted items produces — see ADR-0018. Plugins that omit it are
    # treated as not comparing, so an unaware plugin is never falsely flagged.
    compares_expected: bool

    async def grade(self, output: Any, context: GradeContext, config: dict) -> GradeResult: ...


_graders: dict[str, Grader] = {}


def register_grader(grader: Grader) -> bool:
    """First registration wins; a duplicate is rejected loudly rather than
    silently shadowing what it collides with (plugins.md §2)."""
    if grader.name in _graders:
        log.error("grader %r already registered; ignoring duplicate", grader.name)
        return False
    _graders[grader.name] = grader
    return True


def load_graders() -> None:
    from . import deterministic, judges

    _graders.clear()
    for grader in (*deterministic.BUILTINS, *judges.BUILTINS):
        register_grader(grader)

    if get_settings().deployment_mode == "cloud":
        log.info("cloud mode: skipping external graders (ADR-0012)")
        return

    try:
        from importlib.metadata import entry_points

        for entry_point in entry_points(group=GRADERS_ENTRY_POINT):
            try:
                register_grader(entry_point.load()())
                log.info("loaded grader plugin %r", entry_point.name)
            except Exception:
                log.exception("grader plugin %r failed to load; skipping", entry_point.name)
    except Exception:
        log.exception("grader entry-point discovery failed; using built-ins only")


def get_grader(name: str) -> Grader | None:
    if not _graders:
        load_graders()
    return _graders.get(name)


def registered_names() -> list[str]:
    if not _graders:
        load_graders()
    return sorted(_graders)


def compares_expected(name: str) -> bool:
    """Whether `name` scores against the reference answer (ADR-0018).

    Defaults to False for unknown or unaware graders: a missed flag costs a
    warning nobody sees, while a false one would cry wolf on honest scores.
    """
    grader = get_grader(name)
    return bool(getattr(grader, "compares_expected", False))


def catalog() -> list[dict]:
    """The shared catalog the API serves — see scripts/sync_schemas.py."""
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["graders"]


def _config_schema_of(grader: Grader) -> dict:
    """A plugin's declared config schema, as plain JSON Schema.

    Accepts a dict or a pydantic model (plugins.md §1 writes the contract as
    `config_schema: type[BaseModel]`), and tolerates neither — a plugin with no
    schema is offered with a free-form config editor rather than hidden.
    """
    schema = getattr(grader, "config_schema", None)
    if isinstance(schema, dict):
        return schema
    model_json_schema = getattr(schema, "model_json_schema", None)
    if callable(model_json_schema):
        try:
            return model_json_schema()
        except Exception:  # noqa: BLE001 - a broken schema costs the form, not the grader
            log.warning("grader %r has an unusable config_schema", grader.name, exc_info=True)
    return {"type": "object", "properties": {}, "additionalProperties": True}


def plugin_snapshot() -> list[dict]:
    """Catalog-shaped entries for the graders that are *not* built in.

    Published to Redis so the API can offer plugins in the picker without
    importing the worker (ADR-0023). Built-ins are excluded because the API
    already has them from the shared catalog, and sending them twice would make
    the API arbitrate a conflict it has no reason to see.
    """
    if not _graders:
        load_graders()

    builtin = {g["name"] for g in catalog()}
    entries = []
    for name, grader in sorted(_graders.items()):
        if name in builtin:
            continue
        entries.append(
            {
                "name": name,
                "kind": getattr(grader, "kind", "deterministic"),
                "description": (
                    getattr(grader, "description", None)
                    or f"Third-party grader {name!r} (installed as a plugin)"
                ),
                "config_schema": _config_schema_of(grader),
                "compares_expected": bool(getattr(grader, "compares_expected", False)),
            }
        )
    return entries


async def run_grader(
    name: str, output: Any, context: GradeContext, config: dict
) -> tuple[GradeResult | None, str | None]:
    """Runs one grader under a timeout. Returns (result, error) — never raises.

    An error here is an *item-level* outcome, not a run failure: the caller
    records it and the aggregation rule excludes the item from that grader's
    mean (plan/evaluation.md §3).
    """
    grader = get_grader(name)
    if grader is None:
        return None, f"unknown grader {name!r}"

    settings = get_settings()
    is_judge = getattr(grader, "kind", "deterministic") == "judge"
    timeout = settings.judge_timeout_s if is_judge else settings.grader_timeout_s
    try:
        result = await asyncio.wait_for(grader.grade(output, context, config), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("grader %r timed out after %ss", name, timeout)
        return None, f"grader timed out after {timeout}s"
    except Exception as exc:  # a plugin bug costs one score, not the run
        log.exception("grader %r raised", name)
        return None, f"{type(exc).__name__}: {exc}"[:500]

    return _coerce_result(name, result)


def _coerce_result(name: str, result: Any) -> tuple[GradeResult | None, str | None]:
    """Accept anything shaped like a `GradeResult`, not only the class itself.

    This used to be an `isinstance` check, which quietly made the grader contract
    stricter than plugins.md §1 specifies it: the spec says `Protocol` —
    structural typing — and every other extension point here is duck-typed. An
    `isinstance` gate forces every plugin to `from anvil_worker.graders import
    GradeResult`, which is precisely the dependency on Anvil's internals the
    entry-point design exists to avoid. The example plugin returning its own
    small dataclass is what surfaced it.

    Structural does not mean unchecked. A grader returning a string, or `None`, or
    an object with no usable score is still an item-level error — just diagnosed
    here rather than crashing wherever `.score` was first read.
    """
    if result is None:
        return None, f"grader {name!r} returned None, expected a result with a score"

    score = getattr(result, "score", None)
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None, (
            f"grader {name!r} returned {type(result).__name__} with "
            f"score={score!r}; expected a number 0..1"
        )

    detail = getattr(result, "detail", None)
    return (
        GradeResult(
            # Clamp rather than reject: a plugin returning 1.2 is sloppy, not fatal.
            score=max(0.0, min(1.0, float(score))),
            detail=detail if isinstance(detail, dict) else {},
            reasoning=getattr(result, "reasoning", None),
            tokens_in=getattr(result, "tokens_in", None),
            tokens_out=getattr(result, "tokens_out", None),
            model=getattr(result, "model", None),
        ),
        None,
    )
