"""Plugin registries (plan/plugins.md). Phase 2 builds the pricing registry;
graders (Phase 5) and alert channels (Phase 8) follow the same contract and
land in their own phases.

Two invariants matter more than the mechanism:
- **A plugin can never take down its caller.** Every call is wrapped in
  try/except (checklist row 28). A resolver that raises yields "unknown price",
  which is already a supported outcome.
- **Cloud mode loads built-ins only** (ADR-0012, checklist row 29): third-party
  code does not execute in a deployment that hosts other people's data.
"""

import logging
from collections.abc import Callable

from .config import get_settings

log = logging.getLogger(__name__)

PRICING_ENTRY_POINT = "anvil.pricing"

# (input $/1M tokens, output $/1M tokens)
PriceResolver = Callable[[str], tuple[float, float] | None]

_pricing_resolvers: list[tuple[str, PriceResolver]] = []


def register_pricing_resolver(name: str, resolver: PriceResolver) -> bool:
    """First registration wins; a duplicate name is rejected loudly rather than
    silently shadowing what it collides with."""
    if any(existing == name for existing, _ in _pricing_resolvers):
        log.error("pricing resolver %r already registered; ignoring duplicate", name)
        return False
    _pricing_resolvers.append((name, resolver))
    return True


def load_pricing_resolvers() -> None:
    """Built-ins first, then entry points — so a plugin cannot displace a
    built-in resolver by registering the same name."""
    from .pricing import resolve as builtin_resolve

    _pricing_resolvers.clear()
    register_pricing_resolver("builtin", builtin_resolve)

    if get_settings().deployment_mode == "cloud":
        log.info("cloud mode: skipping external pricing resolvers (ADR-0012)")
        return

    try:
        from importlib.metadata import entry_points

        for entry_point in entry_points(group=PRICING_ENTRY_POINT):
            try:
                register_pricing_resolver(entry_point.name, entry_point.load())
                log.info("loaded pricing resolver %r", entry_point.name)
            except Exception:
                log.exception("pricing resolver %r failed to load; skipping", entry_point.name)
    except Exception:
        log.exception("pricing entry-point discovery failed; using built-ins only")


def resolve_price(model: str | None) -> tuple[float, float] | None:
    """First resolver with an answer wins. Unknown model -> None -> cost null,
    never an error (plan/ingestion.md §4).

    Note for Phase 11: the plugins spec budgets 1 s per pricing call. Built-in
    resolvers are dict lookups, and this runs once per span in a hot batch loop,
    so no per-call timer is imposed here. When external resolvers can do I/O,
    the timeout belongs at that boundary — not on the built-in path.
    """
    if not model:
        return None
    if not _pricing_resolvers:
        load_pricing_resolvers()

    for name, resolver in _pricing_resolvers:
        try:
            price = resolver(model)
        except Exception:
            log.exception("pricing resolver %r raised for model %r; skipping", name, model)
            continue
        if price is not None:
            return price
    return None
