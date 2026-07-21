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

CATALOG_PATH = Path(__file__).resolve().parent.parent / "graders_catalog.json"


@lru_cache
def builtin_graders() -> list[dict]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return data["graders"]


@lru_cache
def builtin_names() -> frozenset[str]:
    return frozenset(g["name"] for g in builtin_graders())
