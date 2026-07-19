"""Built-in model pricing resolver (Phase 2 wires this into cost computation
via the pricing registry — plan/plugins.md). USD per 1M tokens (input, output).
Unknown model -> None -> cost null, never an error. Update alongside provider
price changes; keep entries lowercase."""

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic (verify against current pricing when Phase 2 lands)
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (15.00, 75.00),
    "claude-haiku-4-5": (0.80, 4.00),
    # OpenAI (placeholders — verify)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def resolve(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    return PRICES_PER_MTOK.get(model.lower())
