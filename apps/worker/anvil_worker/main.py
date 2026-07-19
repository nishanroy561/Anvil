"""Worker entrypoint. Phase 0 skeleton: idles with a heartbeat log so the
compose service is healthy. Phase 2 (plan/ingestion.md) replaces the idle
loop with the ingest consumer; later phases add eval_runner, sampler,
alerter, retention as concurrent tasks."""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("anvil_worker")


async def main() -> None:
    log.info("Anvil worker skeleton up. Next: Phase 2 ingest consumer (plan/ingestion.md).")
    while True:
        await asyncio.sleep(60)
        log.info("worker idle heartbeat (no consumers implemented yet)")


if __name__ == "__main__":
    asyncio.run(main())
