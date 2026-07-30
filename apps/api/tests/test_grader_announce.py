"""The picker offers announced plugin graders (ADR-0023).

The API cannot import the worker, so it learns about installed plugins from a
TTL'd Redis snapshot the worker publishes. Every failure here has to degrade to
"built-ins only" rather than to an error: a picker that 500s because a plugin
registry is stale is worse than one that is merely incomplete.
"""

import json

from anvil_api.redis_client import get_redis
from anvil_api.services.graders_catalog import REGISTRY_KEY, builtin_names

from .conftest import requires_services

pytestmark = requires_services


async def announce(*entries: dict) -> None:
    await get_redis().set(
        REGISTRY_KEY, json.dumps({"published_at": "2026-07-30T00:00:00Z", "graders": list(entries)})
    )


PLUGIN = {
    "name": "word_count_bounds",
    "kind": "deterministic",
    "description": "Scores 1.0 when the word count is in range",
    "config_schema": {"type": "object", "properties": {"min_words": {"type": "integer"}}},
    "compares_expected": False,
}


async def graders(client) -> list[dict]:
    res = await client.get("/v1/graders")
    assert res.status_code == 200
    return res.json()


async def test_builtins_are_served_with_no_snapshot(tenant):
    """The default install has no plugins, and must not depend on the key."""
    await get_redis().delete(REGISTRY_KEY)
    listed = await graders(tenant.client)
    assert {g["name"] for g in listed} == set(builtin_names())
    assert all(g["source"] == "builtin" for g in listed)


async def test_an_announced_plugin_appears_in_the_picker(tenant):
    await announce(PLUGIN)
    listed = await graders(tenant.client)

    names = [g["name"] for g in listed]
    assert "word_count_bounds" in names
    # Built-ins first: the list a user recognises does not move because a plugin
    # was installed.
    assert names[: len(builtin_names())] == [g["name"] for g in listed if g["source"] == "builtin"]

    plugin = next(g for g in listed if g["name"] == "word_count_bounds")
    assert plugin["source"] == "plugin"
    assert plugin["description"]
    assert plugin["config_schema"]["properties"]["min_words"]["type"] == "integer"


async def test_a_plugin_cannot_take_over_a_builtin_name(tenant):
    """Second lock. The worker refuses the collision in its own registry; this is
    the side that would otherwise advertise the impostor."""
    await announce({**PLUGIN, "name": "exact_match", "description": "always 1.0"})
    listed = await graders(tenant.client)

    matches = [g for g in listed if g["name"] == "exact_match"]
    assert len(matches) == 1
    assert matches[0]["source"] == "builtin"
    assert matches[0]["description"] != "always 1.0"


async def test_a_plugin_with_no_schema_is_still_offered(tenant):
    """Hiding it would be worse: the grader works, it just has no generated form."""
    await announce({"name": "bare_plugin", "kind": "deterministic"})
    plugin = next(g for g in await graders(tenant.client) if g["name"] == "bare_plugin")
    assert plugin["config_schema"]["additionalProperties"] is True
    assert plugin["description"]


async def test_a_garbage_snapshot_degrades_to_builtins(tenant):
    await get_redis().set(REGISTRY_KEY, "{not json at all")
    listed = await graders(tenant.client)
    assert {g["name"] for g in listed} == set(builtin_names())


async def test_malformed_entries_are_skipped_individually(tenant):
    """One bad entry costs itself, not the whole snapshot."""
    await announce({"kind": "deterministic"}, {"name": ""}, PLUGIN, "not-even-a-dict")
    names = {g["name"] for g in await graders(tenant.client)}
    assert "word_count_bounds" in names
    assert names >= set(builtin_names())


async def test_an_unknown_kind_does_not_break_the_response(tenant):
    """`kind` is a Literal in the response model, so a plugin announcing nonsense
    would 500 the picker if it reached serialization unchecked."""
    await announce({**PLUGIN, "name": "odd_kind", "kind": "wizardry"})
    res = await tenant.client.get("/v1/graders")
    # Either the entry is dropped or it is coerced — what must not happen is a 500.
    assert res.status_code == 200, res.text
    assert set(builtin_names()) <= {g["name"] for g in res.json()}


async def test_plugin_configs_are_still_not_validated(tenant):
    """The announced schema drives the UI; it must not gate requests. It arrived
    from the process being configured, so trusting it to reject an operator's
    input would let a buggy plugin lock its own users out."""
    await announce(PLUGIN)
    dataset = (
        await tenant.client.post(
            f"/v1/projects/{tenant.project_id}/datasets", json={"name": "ds"}
        )
    ).json()
    await tenant.client.post(
        f"/v1/datasets/{dataset['id']}/items",
        json={"items": [{"input": {"q": "x"}, "expected_output": "y"}]},
    )

    run = await tenant.client.post(
        "/v1/eval-runs",
        json={
            "dataset_id": dataset["id"],
            "name": "plugin run",
            "target": {"mode": "rescore"},
            # `min_words` should be an integer per the announced schema. The API
            # accepts it anyway; the worker is what decides.
            "graders": [{"name": "word_count_bounds", "config": {"min_words": "lots"}}],
        },
    )
    assert run.status_code == 201, run.text
