"""Provider resolution (ADR-0016). No network, no keys — construction only.

The rule under test is that the *model name* decides the provider, and that a
missing key is a graceful RuntimeError the caller turns into an item-level
error rather than a crash.
"""

import pytest

from anvil_worker import providers
from anvil_worker.providers import (
    NO_KEY_ERROR,
    build_chat_model,
    default_provider,
    provider_for,
)


# Keys are cleared and `.env` loading is blocked for the whole suite by the
# autouse `_keyless` fixture in conftest.py.


def test_claude_models_resolve_to_anthropic():
    assert provider_for("claude-sonnet-5") == ("ANTHROPIC_API_KEY", "claude-sonnet-5")
    assert provider_for("claude-opus-4-8") == ("ANTHROPIC_API_KEY", "claude-opus-4-8")


def test_gemini_models_resolve_to_google():
    assert provider_for("gemini-2.5-flash") == ("GOOGLE_API_KEY", "gemini-2.5-flash")
    assert provider_for("gemini-2.5-pro") == ("GOOGLE_API_KEY", "gemini-2.5-pro")


def test_resolution_is_case_insensitive():
    assert provider_for("Claude-Sonnet-5") == ("ANTHROPIC_API_KEY", "Claude-Sonnet-5")


def test_unrecognised_model_has_no_provider():
    assert provider_for("llama-3") is None
    assert provider_for(None) is None


def test_default_prefers_anthropic_when_both_keys_are_present(monkeypatch):
    """Order is fixed, not probed — evaluation.md names Anthropic the default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GOOGLE_API_KEY", "y")
    assert default_provider() == ("ANTHROPIC_API_KEY", "claude-sonnet-5")


def test_default_falls_back_to_google_when_only_that_key_is_present(monkeypatch):
    """A Gemini-only deployment still runs judged evals out of the box."""
    monkeypatch.setenv("GOOGLE_API_KEY", "y")
    assert default_provider() == ("GOOGLE_API_KEY", "gemini-2.5-flash")


def test_no_keys_means_no_default():
    assert default_provider() is None


def test_building_without_any_key_is_a_graceful_error():
    with pytest.raises(RuntimeError, match=NO_KEY_ERROR):
        build_chat_model(None)


def test_naming_a_model_whose_key_is_missing_says_which_key(monkeypatch):
    """The error names the missing variable — a dead key must be diagnosable."""
    monkeypatch.setenv("GOOGLE_API_KEY", "y")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_chat_model("claude-sonnet-5")


def test_an_explicit_model_overrides_the_default_provider(monkeypatch):
    """Both keys present, gemini named -> Google, not the default Anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GOOGLE_API_KEY", "y")
    model = build_chat_model("gemini-2.5-flash")
    assert type(model).__name__ == "ChatGoogleGenerativeAI"


def test_claude_model_builds_an_anthropic_handle(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    model = build_chat_model("claude-sonnet-5")
    assert type(model).__name__ == "ChatAnthropic"


def test_unknown_model_with_a_key_present_falls_back_to_the_default(monkeypatch):
    """An unrecognised name is not fatal — the default provider takes it."""
    monkeypatch.setenv("GOOGLE_API_KEY", "y")
    model = build_chat_model("some-unknown-model")
    assert type(model).__name__ == "ChatGoogleGenerativeAI"


def test_every_provider_default_model_is_priced():
    """A default judge with no price entry would silently report cost null."""
    from anvil_worker.pricing import PRICES_PER_MTOK

    for _env, _prefixes, default_model in providers.PROVIDERS:
        assert default_model in PRICES_PER_MTOK, f"{default_model} missing from the price table"
