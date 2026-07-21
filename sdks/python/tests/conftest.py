import json
from pathlib import Path

import pytest

import anvil_sdk
from anvil_sdk._client import AnvilClient

# The SDK is only correct if what it emits validates against the same schema the
# ingest endpoint enforces (`additionalProperties: false`) — so tests validate
# against the shared source of truth, not a copy.
SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "packages" / "shared" / "trace-schema.json"
)


@pytest.fixture(scope="session")
def wire_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def validate_batch(wire_schema):
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(wire_schema)

    def check(batch: list) -> None:
        errors = sorted(validator.iter_errors({"traces": batch}), key=lambda e: e.path)
        assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)

    return check


@pytest.fixture
def client():
    """A client with no background thread: tests drive `flush()` themselves so
    nothing depends on timing."""
    instance = AnvilClient(
        api_key="ak_test",
        base_url="https://anvil.test",
        start_worker=False,
    )
    anvil_sdk.set_client(instance)
    yield instance
    anvil_sdk.set_client(None)
