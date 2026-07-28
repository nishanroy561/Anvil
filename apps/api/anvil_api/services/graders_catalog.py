"""Built-in grader catalog, read from the vendored copy of
`packages/shared/graders.json` (ADR-0014, scripts/sync_schemas.py).

The API serves this list to drive the UI's grader picker and validates run
configs against it. Implementations live in the worker; the API never imports
them — the catalog is the contract between the two.

Third-party graders (the `anvil.graders` entry point) are additive at runtime in
the worker and are deliberately *not* validated here: the API can't see a plugin
installed only in the worker image, so an unknown grader name is accepted with a
warning rather than rejected. A grader the worker can't resolve fails at the
item level, which is already a supported outcome (plan/evaluation.md §3).
"""

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

CATALOG_PATH = Path(__file__).resolve().parent.parent / "graders_catalog.json"


@lru_cache
def builtin_graders() -> list[dict]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return data["graders"]


@lru_cache
def builtin_names() -> frozenset[str]:
    return frozenset(g["name"] for g in builtin_graders())


@lru_cache
def _schema_for(name: str) -> str | None:
    for grader in builtin_graders():
        if grader["name"] == name:
            return json.dumps(grader.get("config_schema") or {})
    return None


def validate_grader_config(name: str, config: dict | None) -> str | None:
    """Returns a human-readable problem with `config`, or None if it is fine.

    This matters more than it looks. Several graders treat a missing config key
    as a vacuous default — `contains` with no `value` searches for the empty
    string, which every output contains, so a typo'd key produces a grader that
    scores a confident 1.0 on everything and measures nothing. A run like that
    is worse than a failed one: it looks like a pass.

    Unknown graders are not validated — they may be worker-side plugins the API
    cannot see (see the module docstring).
    """
    raw = _schema_for(name)
    if raw is None:
        return None
    try:
        jsonschema.validate(config or {}, json.loads(raw))
    except jsonschema.ValidationError as exc:
        where = ".".join(str(p) for p in exc.absolute_path)
        return f"{where}: {exc.message}" if where else exc.message
    return None
