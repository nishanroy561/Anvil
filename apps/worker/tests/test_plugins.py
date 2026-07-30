"""Plugin discovery and isolation (Phase 11 §5, plan/plugins.md §5, ADR-0012).

Two halves. The first uses the real `examples/plugin-example` package if it is
installed, so the shipped author template is held to the contract it documents.
The second registers deliberately broken plugins directly — a raising one, a
hanging one, one returning nonsense — because the guarantee that matters is not
"good plugins work" but **"a bad plugin cannot take the run or the worker down"**.

No services needed: registries and `run_grader` are pure in-process code.
"""

import asyncio
from dataclasses import dataclass, field
from importlib.metadata import entry_points

import pytest

from anvil_worker import channels as ch
from anvil_worker.graders import (
    GRADERS_ENTRY_POINT,
    GradeContext,
    GradeResult,
    get_grader,
    load_graders,
    register_grader,
    run_grader,
)

EXAMPLE_INSTALLED = any(
    ep.name == "word_count_bounds" for ep in entry_points(group=GRADERS_ENTRY_POINT)
)
requires_example = pytest.mark.skipif(
    not EXAMPLE_INSTALLED,
    reason="needs the example plugin: pip install -e examples/plugin-example",
)


@pytest.fixture(autouse=True)
def fresh_registries():
    """Each test starts from a reloaded registry, so one test's broken plugin is
    never visible to the next."""
    load_graders()
    ch.load_channels()
    yield
    load_graders()
    ch.load_channels()


def ctx(**kwargs) -> GradeContext:
    return GradeContext(**kwargs)


# ---- the shipped example -----------------------------------------------------


@requires_example
def test_the_example_grader_is_discovered():
    grader = get_grader("word_count_bounds")
    assert grader is not None
    assert grader.name == "word_count_bounds"
    # The attributes the eval-run picker and the tautology check read.
    assert getattr(grader, "kind", "deterministic") == "deterministic"
    assert getattr(grader, "compares_expected", False) is False


@requires_example
def test_the_example_channel_is_discovered():
    assert ch.get_channel("slack") is not None
    assert ch.get_channel("webhook") is not None, "built-ins still load alongside"


@requires_example
async def test_the_example_grader_scores_both_ways():
    result, error = await run_grader(
        "word_count_bounds", "one two three four five", ctx(), {"min_words": 3, "max_words": 10}
    )
    assert error is None
    assert result.score == 1.0
    assert result.detail["words"] == 5

    result, error = await run_grader(
        "word_count_bounds", "too short", ctx(), {"min_words": 5, "max_words": 10}
    )
    assert error is None
    assert result.score == 0.0


@requires_example
async def test_the_example_grader_handles_a_dict_output():
    """Outputs are JSON, so a grader assuming `str` breaks on every trace whose
    output is an object — which for a chat app is all of them."""
    result, error = await run_grader(
        "word_count_bounds",
        {"answer": "refunds take five to seven business days"},
        ctx(),
        {"min_words": 3, "max_words": 20},
    )
    assert error is None
    assert result.score == 1.0


@requires_example
async def test_the_example_grader_defends_its_own_config():
    """Anvil validates *built-in* configs against a schema; a plugin's it cannot
    see, so the plugin checks its own."""
    result, error = await run_grader(
        "word_count_bounds", "anything", ctx(), {"min_words": 10, "max_words": 2}
    )
    assert error is None, "a bad config is a scored explanation, not a crash"
    assert result.score == 0.0
    assert "error" in result.detail


@requires_example
async def test_the_example_channel_builds_a_slack_payload(monkeypatch):
    """The reason the channel registry exists: Anvil's built-in webhook is
    vendor-neutral on purpose, so the vendor shape is the plugin's job."""
    sent: dict = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json):
            sent.update(url=url, payload=json)
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    await ch.get_channel("slack").send(
        {"value": 0.29, "fired_at": None, "resolved_at": None},
        {
            "id": "r1",
            "name": "quality dropped",
            "metric": "eval_score",
            "comparison": "lt",
            "threshold": 0.5,
            "window_minutes": 120,
            "project_id": "p1",
        },
        {"url": "https://hooks.slack.test/abc"},
    )

    assert sent["url"] == "https://hooks.slack.test/abc"
    payload = sent["payload"]
    # `text` is the notification preview and the a11y fallback; blocks alone
    # render as an empty message in some Slack surfaces.
    assert payload["text"]
    assert payload["blocks"][0]["type"] == "section"
    # Plain language, not a rule definition — the reader is on their phone.
    assert "below 0.5" in payload["text"]
    assert "eval score" in payload["text"]


@requires_example
async def test_a_channel_with_no_url_is_not_an_error():
    """A project with no Slack URL configured is the common case."""
    await ch.get_channel("slack").send({}, {"name": "r"}, {})


# ---- isolation: a bad plugin cannot take anything down -----------------------


@dataclass
class OwnResult:
    """A plugin's own result type — no import from `anvil_worker`.

    This is the shape the contract promises to accept (plugins.md §1 says
    `Protocol`), and pinning it here is what keeps an `isinstance` check from
    creeping back in and forcing every plugin to depend on Anvil's internals.
    """

    score: float
    detail: dict = field(default_factory=dict)


class RaisingGrader:
    name = "explodes"
    kind = "deterministic"
    compares_expected = False

    async def grade(self, output, context, config):
        raise RuntimeError("plugin author had a bad day")


class HangingGrader:
    name = "hangs"
    kind = "deterministic"
    compares_expected = False

    async def grade(self, output, context, config):
        await asyncio.sleep(3600)


class NonsenseGrader:
    name = "nonsense"
    kind = "deterministic"
    compares_expected = False

    async def grade(self, output, context, config):
        return "not a result at all"


class DuckTypedGrader:
    name = "duck_typed"
    kind = "deterministic"
    compares_expected = False

    async def grade(self, output, context, config):
        return OwnResult(score=0.75, detail={"via": "its own dataclass"})


class OutOfRangeGrader:
    name = "out_of_range"
    kind = "deterministic"
    compares_expected = False

    async def grade(self, output, context, config):
        return OwnResult(score=42.0)


async def test_a_raising_grader_costs_one_score(monkeypatch):
    register_grader(RaisingGrader())
    result, error = await run_grader("explodes", "x", ctx(), {})
    assert result is None
    assert "RuntimeError" in error and "bad day" in error
    # And the registry is intact, so the next grader in the run still works.
    assert get_grader("exact_match") is not None


async def test_a_hanging_grader_is_cut_off(monkeypatch):
    """Without this the run never finishes and the worker looks wedged."""
    from anvil_worker.config import get_settings

    monkeypatch.setattr(get_settings(), "grader_timeout_s", 0.05, raising=False)
    register_grader(HangingGrader())

    result, error = await asyncio.wait_for(run_grader("hangs", "x", ctx(), {}), timeout=5)
    assert result is None
    assert "timed out" in error


async def test_a_grader_returning_nonsense_is_an_item_error():
    register_grader(NonsenseGrader())
    result, error = await run_grader("nonsense", "x", ctx(), {})
    assert result is None
    assert "expected a number" in error


async def test_a_plugins_own_result_type_is_accepted():
    """The contract is structural (plugins.md §1). Requiring Anvil's own class
    would force every plugin to import from `anvil_worker`."""
    register_grader(DuckTypedGrader())
    result, error = await run_grader("duck_typed", "x", ctx(), {})
    assert error is None
    assert isinstance(result, GradeResult)
    assert result.score == 0.75
    assert result.detail == {"via": "its own dataclass"}


async def test_an_out_of_range_score_is_clamped_not_rejected():
    """A plugin returning 42 is sloppy, not fatal — aggregation just needs 0..1."""
    register_grader(OutOfRangeGrader())
    result, error = await run_grader("out_of_range", "x", ctx(), {})
    assert error is None
    assert result.score == 1.0


async def test_an_unknown_grader_is_an_error_not_a_crash():
    result, error = await run_grader("no_such_grader", "x", ctx(), {})
    assert result is None
    assert "unknown grader" in error


# ---- name collisions ---------------------------------------------------------


def test_a_colliding_grader_name_is_rejected_loudly(caplog):
    """First registration wins. Built-ins register first, so a plugin cannot
    quietly replace `exact_match` with something that always returns 1.0."""
    import logging

    original = get_grader("exact_match")

    class Impostor:
        name = "exact_match"
        kind = "deterministic"
        compares_expected = False

        async def grade(self, output, context, config):
            return OwnResult(score=1.0)

    with caplog.at_level(logging.ERROR):
        assert register_grader(Impostor()) is False
    assert "already registered" in caplog.text
    assert get_grader("exact_match") is original, "the built-in survived"


def test_a_colliding_channel_name_is_rejected_loudly(caplog):
    import logging

    original = ch.get_channel("webhook")

    class Impostor:
        name = "webhook"

        async def send(self, event, rule, config):
            return None

    with caplog.at_level(logging.ERROR):
        assert ch.register_channel(Impostor()) is False
    assert "already registered" in caplog.text
    assert ch.get_channel("webhook") is original


# ---- cloud mode (ADR-0012) ---------------------------------------------------


@requires_example
def test_cloud_mode_ignores_external_plugins(monkeypatch):
    """Arbitrary third-party Python must not execute next to other tenants' data.
    The plugin is unchanged; the host declines to load it."""
    from anvil_worker.config import get_settings

    monkeypatch.setattr(get_settings(), "deployment_mode", "cloud", raising=False)
    load_graders()
    ch.load_channels()

    assert get_grader("word_count_bounds") is None
    assert ch.get_channel("slack") is None
    # Built-ins are unaffected — cloud mode is not a degraded mode.
    assert get_grader("exact_match") is not None
    assert ch.get_channel("webhook") is not None


# ---- announcing the registry to the API (ADR-0023) ---------------------------


@requires_example
def test_the_snapshot_carries_plugins_and_not_builtins():
    """Built-ins are excluded on purpose: the API already has them from the shared
    catalog, and sending them twice would make it arbitrate a conflict it has no
    reason to see."""
    from anvil_worker.graders import plugin_snapshot

    entries = {e["name"]: e for e in plugin_snapshot()}
    assert "word_count_bounds" in entries
    assert "exact_match" not in entries, "built-ins stay out of the snapshot"

    entry = entries["word_count_bounds"]
    assert entry["kind"] == "deterministic"
    assert entry["description"]
    assert entry["config_schema"]["type"] == "object"
    assert entry["compares_expected"] is False


def test_a_plugin_with_no_schema_gets_a_permissive_one():
    """It is offered with a free-form config editor rather than hidden."""
    from anvil_worker.graders import plugin_snapshot

    register_grader(DuckTypedGrader())
    entry = next(e for e in plugin_snapshot() if e["name"] == "duck_typed")
    assert entry["config_schema"]["additionalProperties"] is True


def test_a_pydantic_config_schema_is_accepted():
    """plugins.md §1 writes the contract as `config_schema: type[BaseModel]`, so a
    pydantic model has to work as well as a plain dict."""
    from pydantic import BaseModel

    from anvil_worker.graders import plugin_snapshot

    class Config(BaseModel):
        threshold: float = 0.5

    class Declared:
        name = "declares_schema"
        kind = "judge"
        compares_expected = False
        description = "A plugin that declares its config"
        config_schema = Config

        async def grade(self, output, context, config):
            return OwnResult(score=1.0)

    register_grader(Declared())
    entry = next(e for e in plugin_snapshot() if e["name"] == "declares_schema")
    assert entry["kind"] == "judge"
    assert entry["description"] == "A plugin that declares its config"
    assert "threshold" in entry["config_schema"]["properties"]


def test_a_broken_config_schema_costs_the_form_not_the_grader(caplog):
    import logging

    from anvil_worker.graders import plugin_snapshot

    class Exploding:
        @staticmethod
        def model_json_schema():
            raise RuntimeError("schema generation is broken")

    class Broken:
        name = "broken_schema"
        kind = "deterministic"
        compares_expected = False
        config_schema = Exploding

        async def grade(self, output, context, config):
            return OwnResult(score=1.0)

    register_grader(Broken())
    with caplog.at_level(logging.WARNING):
        entry = next(e for e in plugin_snapshot() if e["name"] == "broken_schema")

    assert entry["config_schema"]["additionalProperties"] is True
    assert get_grader("broken_schema") is not None, "the grader itself still works"


async def test_announcing_never_raises_when_redis_is_down():
    """A worker that cannot announce itself still grades; it just isn't listed."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    from anvil_worker.announce import Announcer

    class DeadRedis:
        async def set(self, *_a, **_kw):
            raise RedisConnectionError("no route to host")

    assert await Announcer(DeadRedis()).tick() == 0


async def test_announcing_writes_a_ttld_snapshot():
    from anvil_worker.announce import REGISTRY_KEY, TTL_S, Announcer

    written: dict = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            written.update(key=key, value=value, ex=ex)

    register_grader(DuckTypedGrader())
    count = await Announcer(FakeRedis()).tick()

    assert count >= 1
    assert written["key"] == REGISTRY_KEY
    # The TTL is what makes a removed plugin disappear on its own.
    assert written["ex"] == TTL_S
    import json as _json

    payload = _json.loads(written["value"])
    assert payload["published_at"]
    assert any(g["name"] == "duck_typed" for g in payload["graders"])


def test_a_plugin_that_fails_to_load_does_not_stop_the_others(monkeypatch, caplog):
    """Discovery is per-plugin: one broken package costs itself, not the registry."""
    import logging

    class BadEntryPoint:
        name = "imports_badly"

        def load(self):
            raise ImportError("no module named 'wishful_thinking'")

    class GoodEntryPoint:
        name = "fine"

        def load(self):
            return DuckTypedGrader

    # `load_graders` imports `entry_points` from importlib.metadata at call time,
    # so the source module is what has to be patched.
    import importlib.metadata as md

    monkeypatch.setattr(
        md, "entry_points", lambda group=None: [BadEntryPoint(), GoodEntryPoint()]
    )

    with caplog.at_level(logging.ERROR):
        load_graders()

    assert get_grader("duck_typed") is not None, "the healthy plugin still loaded"
    assert get_grader("exact_match") is not None, "and so did the built-ins"
    assert "imports_badly" in caplog.text
