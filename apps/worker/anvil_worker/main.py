"""Worker entrypoint: every background loop runs as a concurrent task here —
ingest consumer, stale-trace reaper, feedback consumer, eval runner, monitor
sampler and alerter (plan/ingestion.md §3, plan/evaluation.md §3/§6/§7).

Phase 10 adds retention alongside these.
"""

import asyncio
import logging
import signal

import redis.asyncio as redis
from prometheus_client import start_http_server

from . import dogfood
from .alerter import Alerter
from .channels import load_channels
from .config import get_settings
from .eval_runner import EvalRunner
from .feedback_consumer import FeedbackConsumer
from .graders import load_graders
from .ingest_consumer import IngestConsumer, run_reaper
from .providers import load_env
from .registry import load_pricing_resolvers
from .sampler import Sampler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("anvil_worker")


async def main() -> None:
    # Judge/target keys come from the repo-root .env before anything reads them.
    load_env()
    settings = get_settings()
    start_http_server(settings.metrics_port)
    load_pricing_resolvers()
    load_graders()
    load_channels()

    client = redis.from_url(settings.redis_url, decode_responses=True)
    consumer = IngestConsumer(client)
    feedback = FeedbackConsumer(client)
    evals = EvalRunner(client)
    sampler = Sampler()
    alerter = Alerter()

    tasks = [
        asyncio.create_task(consumer.run()),
        asyncio.create_task(run_reaper()),
        asyncio.create_task(feedback.run()),
        asyncio.create_task(evals.run()),
        asyncio.create_task(sampler.run()),
        asyncio.create_task(alerter.run()),
    ]

    def _request_stop() -> None:
        # Stop reading new work; the in-flight batch finishes and acks, so a
        # clean shutdown leaves nothing pending for reclaim to mop up.
        log.info("shutdown signal received; draining")
        consumer.stop()
        feedback.stop()
        evals.stop()
        sampler.stop()
        alerter.stop()

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
        # Ships whatever judge traces are still queued before the process goes.
        dogfood.reset()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("worker stopped")
