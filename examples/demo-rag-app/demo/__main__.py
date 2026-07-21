"""CLI: `python -m demo serve` / `python -m demo seed --n 200`."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    # Connection flags live on a parent parser so they work on either side of
    # the subcommand — `demo seed --api-key X` is the order people actually type.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--api-key", default=os.getenv("ANVIL_API_KEY"), help="Anvil project API key"
    )
    common.add_argument(
        "--base-url",
        default=os.getenv("ANVIL_BASE_URL", "http://localhost:8000"),
        help="Anvil API base URL",
    )

    parser = argparse.ArgumentParser(
        prog="demo", description="Anvil demo RAG app", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the FastAPI app", parents=[common])
    serve.add_argument("--port", type=int, default=8100)

    seed_cmd = sub.add_parser("seed", help="generate demo traces", parents=[common])
    seed_cmd.add_argument("--n", type=int, default=200)
    seed_cmd.add_argument(
        "--delay", type=float, default=0.0, help="seconds between traces"
    )

    args = parser.parse_args(argv)

    if not args.api_key:
        print(
            "No API key. Pass --api-key or set ANVIL_API_KEY.\n"
            "Get one from `python -m anvil_api.seed` in apps/api.",
            file=sys.stderr,
        )
        return 2

    os.environ["ANVIL_API_KEY"] = args.api_key
    os.environ["ANVIL_BASE_URL"] = args.base_url

    if args.command == "serve":
        import uvicorn

        uvicorn.run("demo.app:app", host="127.0.0.1", port=args.port)
        return 0

    import anvil_sdk as anvil

    from .seed import seed

    anvil.init(api_key=args.api_key, base_url=args.base_url)
    print(f"seeding {args.n} traces -> {args.base_url}")
    seed(n=args.n, delay=args.delay)
    anvil.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
