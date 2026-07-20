"""Copy the shared wire-format schema into each package that must ship it.

`packages/shared/trace-schema.json` is the single source of truth
(plan/architecture.md §4). Service images build with their own directory as the
Docker context, so they cannot COPY from `packages/` — each needs a vendored
copy inside its package. Those copies are build artifacts, never edited by hand:
run this after changing the shared schema. `test_schema_sync.py` fails if a copy
drifts.

Usage:  python scripts/sync_schemas.py [--check]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packages" / "shared" / "trace-schema.json"
COPIES = [ROOT / "apps" / "api" / "anvil_api" / "trace_schema.json"]


def main() -> int:
    check_only = "--check" in sys.argv
    source_bytes = SOURCE.read_bytes()
    drifted = []

    for copy in COPIES:
        current = copy.read_bytes() if copy.exists() else None
        if current == source_bytes:
            continue
        drifted.append(copy)
        if not check_only:
            copy.write_bytes(source_bytes)

    rel = [str(p.relative_to(ROOT)) for p in drifted]
    if check_only and drifted:
        print("Out of sync with packages/shared/trace-schema.json:")
        for path in rel:
            print(f"  {path}")
        print("Run: python scripts/sync_schemas.py")
        return 1
    print(f"Synced: {', '.join(rel)}" if drifted else "All schema copies up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
