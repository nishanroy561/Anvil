"""Built-in grader catalog, read from the vendored copy of
`packages/shared/graders.json` (ADR-0014, scripts/sync_schemas.py).

The API serves this list to drive the UI's grader picker and validates run
configs against it. Implementations live in the worker; the API never imports
them — the catalog is the contract between the two.

Third-party graders (the `anvil.graders` entry point) live in the worker. The API
learns about them from a TTL'd snapshot the worker publishes to Redis (ADR-0023)
and merges them into the picker — but still does **not validate their configs**.
The schema in that snapshot came from the very process being configured, so
trusting it to reject an operator's input would let a buggy plugin lock its own
users out of a form. An unknown grader name is accepted with a warning; a grader
the worker can't resolve fails at the item level, which is already a supported
outcome (plan/evaluation.md §3).
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

import jsonschema
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

# Mirrors `anvil_worker.announce.REGISTRY_KEY` — wire protocol between the two
# services, so a change here must be made in both places (ADR-0014).
REGISTRY_KEY = "anvil:registry:graders"

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


async def announced_plugins(redis) -> list[dict]:
    """Plugin graders the worker has announced (ADR-0023).

    Returns `[]` on every failure — a missing key, an unparseable blob, a Redis
    outage. The picker degrading to built-ins beats an error page, and this is not
    a load-bearing read: the worker grades whatever it has regardless.
    """
    try:
        raw = await redis.get(REGISTRY_KEY)
    except RedisError:
        log.debug("grader registry unavailable; offering built-ins only", exc_info=True)
        return []
    if not raw:
        return []

    try:
        entries = json.loads(raw).get("graders") or []
    except (ValueError, AttributeError):
        log.warning("grader registry snapshot is not usable JSON; ignoring it")
        return []

    known = builtin_names()
    out = []
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        # A built-in's name can never be taken over from here. The worker already
        # refuses the collision in its own registry; this is the second lock, on
        # the side that would otherwise be advertising the impostor.
        if not isinstance(name, str) or not name or name in known:
            continue
        # `kind` is a two-value Literal on the response model, so an unexpected
        # value would 500 the whole picker at serialization time — one plugin
        # announcing nonsense must not cost everyone else their grader list.
        kind = entry.get("kind")
        if kind not in ("deterministic", "judge"):
            log.warning("plugin grader %r announced kind=%r; treating as deterministic", name, kind)
            kind = "deterministic"

        out.append(
            {
                "name": name,
                "kind": kind,
                "description": entry.get("description") or f"Plugin grader {name!r}",
                "config_schema": entry.get("config_schema")
                or {"type": "object", "properties": {}, "additionalProperties": True},
                "compares_expected": bool(entry.get("compares_expected", False)),
                "source": "plugin",
            }
        )
    return out


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
