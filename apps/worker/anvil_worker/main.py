"""Worker entrypoint: runs the ingest consumer and the stale-trace reaper as
concurrent tasks (plan/ingestion.md §3).

Later phases add eval_runner, sampler, alerter, and retention alongside these.
"""

import asyncio
import logging
import signal

import redis.asyncio as redis
from prometheus_client import start_http_server

from .config import get_settings
from .ingest_consumer import IngestConsumer, run_reaper
from .registry import load_pricing_resolvers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("anvil_worker")


async def main() -> None:
    settings = get_settings()
    start_http_server(settings.metrics_port)
    load_pricing_resolvers()

    client = redis.from_url(settings.redis_url, decode_responses=True)
    consumer = IngestConsumer(client)

    tasks = [asyncio.create_task(consumer.run()), asyncio.create_task(run_reaper())]

    def _request_stop() -> None:
        # Stop reading new work; the in-flight batch finishes and acks, so a
        # clean shutdown leaves nothing pending for reclaim to mop up.
        log.info("shutdown signal received; draining")
        consumer.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows dev boxes: fall back to KeyboardInterrupt handling.
            pass

    log.info("anvil worker up (metrics on :%d)", settings.metrics_port)
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("worker stopped")
