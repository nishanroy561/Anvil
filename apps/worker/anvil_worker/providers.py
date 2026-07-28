"""LLM provider resolution for judges and target generation (ADR-0016, ADR-0020).

The model name picks the provider — `claude-*` → Anthropic, `gemini-*` →
Google. That keeps provider choice in data (grader/target config, already
round-tripped through the API and UI) rather than in configuration, so adding a
provider never touches the eval runner or the dashboard.

Groq breaks the "family name implies provider" assumption, because Groq is an
inference host rather than a model family: it serves `llama-3.3-70b-versatile`
and `openai/gpt-oss-120b`, names that say nothing about who is serving them and
in the second case actively mislead. So Groq models are addressed with an
explicit `groq/` routing prefix — `groq/llama-3.3-70b-versatile` — which the
first segment of is stripped before the call (ADR-0020).

Two behaviours are deliberate:

- **Keys are read from the process environment**, loaded once from the repo-root
  `.env` by `load_env()`. Settings objects deliberately aren't used here: the
  worker's pydantic settings resolve `.env` against the working directory, and
  a judge that silently sees no key depending on where you launched it is a
  debugging trap.
- **A missing key is a graceful item-level error**, never a crash — the suite
  must run keyless (CLAUDE.md), so `no judge key configured` propagates as the
  item's error and the run continues.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

NO_KEY_ERROR = "no judge key configured"

# (env var, model-name prefixes, default model) in preference order.
PROVIDERS = [
    ("ANTHROPIC_API_KEY", ("claude",), "claude-sonnet-5"),
    ("GOOGLE_API_KEY", ("gemini",), "gemini-2.5-flash"),
    ("GROQ_API_KEY", ("groq/",), "groq/llama-3.3-70b-versatile"),
]

GROQ_PREFIX = "groq/"

_env_loaded = False


def load_env() -> None:
    """Loads the repo-root `.env` into `os.environ`, once.

    Resolved from this file's location rather than the CWD: the worker is
    launched from `apps/worker` in dev and from `/app` in the image, and a key
    that only works from one of those is worse than no key at all. Real
    environment variables always win — compose and CI set them directly.
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True

    root = Path(__file__).resolve().parents[3] / ".env"
    if not root.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(root, override=False)
        log.info("loaded environment from %s", root)
    except ImportError:
        log.warning("python-dotenv not installed; %s not loaded", root)


def provider_for(model: str | None) -> tuple[str, str] | None:
    """(env var, model) for an explicit model name, or None if unrecognised."""
    if not model:
        return None
    lowered = model.lower()
    for env_var, prefixes, _default in PROVIDERS:
        if any(lowered.startswith(p) for p in prefixes):
            return env_var, model
    return None


def default_provider() -> tuple[str, str] | None:
    """First provider with a key present — how "just use whatever works" works.

    Anthropic is first because evaluation.md names it the default judge; Google
    is the fallback so a Gemini-only box can still run judged evals.
    """
    for env_var, _prefixes, default_model in PROVIDERS:
        if os.getenv(env_var):
            return env_var, default_model
    return None


def build_chat_model(model: str | None, **kwargs):
    """Constructs a LangChain chat model for `model`.

    Raises RuntimeError with a human-readable reason — the caller turns that
    into an item-level error rather than failing the run.
    """
    load_env()

    resolved = provider_for(model) or default_provider()
    if resolved is None:
        raise RuntimeError(NO_KEY_ERROR)

    env_var, model_name = resolved
    if not os.getenv(env_var):
        raise RuntimeError(f"{NO_KEY_ERROR} ({env_var} is unset for model {model_name!r})")

    if env_var == "ANTHROPIC_API_KEY":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise RuntimeError(f"langchain-anthropic not installed: {exc}") from exc
        # No temperature: current Claude models reject non-default sampling
        # parameters, and the judge wants determinism anyway.
        return ChatAnthropic(model=model_name, max_tokens=kwargs.get("max_tokens", 1024))

    if env_var == "GROQ_API_KEY":
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError(f"langchain-groq not installed: {exc}") from exc
        # Only the first segment is the routing prefix; the rest is Groq's own
        # model id, which may itself contain a slash (`openai/gpt-oss-120b`).
        return ChatGroq(
            model=model_name[len(GROQ_PREFIX) :],
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise RuntimeError(f"langchain-google-genai not installed: {exc}") from exc
    return ChatGoogleGenerativeAI(
        model=model_name,
        max_output_tokens=kwargs.get("max_tokens", 1024),
    )
