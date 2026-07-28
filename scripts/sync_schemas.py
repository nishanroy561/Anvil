"""Copy the shared contract files into each package that must ship them.

`packages/shared/` holds the single sources of truth (plan/architecture.md §4):
the trace wire format, and the built-in grader catalog. Service images build
with their own directory as the Docker context, so they cannot COPY from
`packages/` — each needs a vendored copy inside its package. Those copies are
build artifacts, never edited by hand: run this after changing a shared file.
`test_schema_sync.py` (API) and `test_grader_catalog.py` (worker) fail if a copy
drifts.

Usage:  python scripts/sync_schemas.py [--check]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "packages" / "shared"

# source -> the vendored copies that must match it byte for byte
SYNCED: dict[Path, list[Path]] = {
    SHARED / "trace-schema.json": [ROOT / "apps" / "api" / "anvil_api" / "trace_schema.json"],
    SHARED / "graders.json": [
        ROOT / "apps" / "api" / "anvil_api" / "graders_catalog.json",
        ROOT / "apps" / "worker" / "anvil_worker" / "graders_catalog.json",
    ],
}


def main() -> int:
    check_only = "--check" in sys.argv
    drifted = []

    for source, copies in SYNCED.items():
        source_bytes = source.read_bytes()
        for copy in copies:
            current = copy.read_bytes() if copy.exists() else None
            if current == source_bytes:
                continue
            drifted.append(copy)
            if not check_only:
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(source_bytes)

    rel = [str(p.relative_to(ROOT)) for p in drifted]
    if check_only and drifted:
        print("Out of sync with packages/shared/:")
        for path in rel:
            print(f"  {path}")
        print("Run: python scripts/sync_schemas.py")
        return 1
    print(f"Synced: {', '.join(rel)}" if drifted else "All shared copies up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
