"""The wire format is defined once (plan/architecture.md §4).

`anvil_api/trace_schema.json` is a vendored build artifact — service images
build from their own directory and can't COPY `packages/shared/`. This test is
what keeps the copy honest; it needs no services.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED = REPO_ROOT / "packages" / "shared" / "trace-schema.json"
VENDORED = REPO_ROOT / "apps" / "api" / "anvil_api" / "trace_schema.json"


def test_vendored_schema_matches_shared_source_of_truth():
    assert SHARED.exists(), "source of truth missing"
    assert VENDORED.read_bytes() == SHARED.read_bytes(), (
        "anvil_api/trace_schema.json drifted from packages/shared/trace-schema.json — "
        "run: python scripts/sync_schemas.py"
    )
