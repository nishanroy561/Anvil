"""Built-in model pricing resolver (Phase 2 wires this into cost computation
via the pricing registry — plan/plugins.md). USD per 1M tokens (input, output).
Unknown model -> None -> cost null, never an error. Update alongside provider
price changes; keep entries lowercase."""

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic — verified 2026-07-21 against the published price list.
    # Sonnet 5 carries an introductory $2/$10 through 2026-08-31; the list
    # price is used here so cost is never under-reported.
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Google (ADR-0016 — Gemini is a supported judge/target provider)
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    # OpenAI (placeholders — verify)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def resolve(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    return PRICES_PER_MTOK.get(model.lower())
