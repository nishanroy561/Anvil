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
    # OpenAI — verified 2026-11-08 against developers.openai.com/api/docs/pricing
    # (standard tier; batch and priority are priced differently).
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    # Groq — verified 2026-07-28 against groq.com/pricing. Keyed on the full
    # `groq/` name (ADR-0020) because the provider, not the model family, sets
    # the price: `openai/gpt-oss-120b` on Groq costs nothing like it would from
    # OpenAI, and both could appear in one deployment.
    "groq/llama-3.3-70b-versatile": (0.59, 0.79),
    "groq/llama-3.1-8b-instant": (0.05, 0.08),
    "groq/openai/gpt-oss-120b": (0.15, 0.60),
    "groq/openai/gpt-oss-20b": (0.075, 0.30),
    "groq/qwen/qwen3.6-27b": (0.60, 3.00),
    # `groq/compound` is deliberately absent: Groq passes its cost through to
    # whichever underlying models the system picks, so there is no per-token
    # price to state. Unknown model -> None -> cost null, which is the honest
    # answer rather than a made-up one.
}


def resolve(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    return PRICES_PER_MTOK.get(model.lower())
