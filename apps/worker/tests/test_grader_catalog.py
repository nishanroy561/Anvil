"""The grader catalog is a contract between the API and the worker.

`packages/shared/graders.json` is the single source of truth: the API serves it
to drive the UI's grader picker and to validate run configs, while the worker
implements the graders. Neither imports the other (ADR-0014), so this test is
what keeps them honest. Needs no services.
"""

import json
from pathlib import Path

from anvil_worker.graders import catalog, load_graders, registered_names

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED = REPO_ROOT / "packages" / "shared" / "graders.json"
WORKER_COPY = REPO_ROOT / "apps" / "worker" / "anvil_worker" / "graders_catalog.json"
API_COPY = REPO_ROOT / "apps" / "api" / "anvil_api" / "graders_catalog.json"


def test_vendored_copies_match_the_shared_source_of_truth():
    assert SHARED.exists(), "source of truth missing"
    for copy in (WORKER_COPY, API_COPY):
        assert copy.read_bytes() == SHARED.read_bytes(), (
            f"{copy.name} drifted from packages/shared/graders.json — "
            "run: python scripts/sync_schemas.py"
        )


def test_every_catalogued_grader_has_an_implementation():
    """A grader in the picker that the worker can't run is a broken promise."""
    load_graders()
    implemented = set(registered_names())
    catalogued = {g["name"] for g in catalog()}
    assert catalogued <= implemented, f"catalogued but unimplemented: {catalogued - implemented}"


def test_every_builtin_implementation_is_catalogued():
    """An implemented grader missing from the catalog is invisible in the UI."""
    load_graders()
    implemented = set(registered_names())
    catalogued = {g["name"] for g in catalog()}
    assert implemented <= catalogued, f"implemented but uncatalogued: {implemented - catalogued}"


def test_catalog_entries_are_well_formed():
    for entry in catalog():
        assert entry["kind"] in ("deterministic", "judge"), entry["name"]
        assert entry["description"].strip(), entry["name"]
        schema = entry["config_schema"]
        assert schema["type"] == "object"
        # additionalProperties:false keeps the UI's generated form honest.
        assert schema.get("additionalProperties") is False, entry["name"]


def test_shared_catalog_is_valid_json_with_the_expected_shape():
    data = json.loads(SHARED.read_text(encoding="utf-8"))
    assert isinstance(data["graders"], list) and data["graders"]
