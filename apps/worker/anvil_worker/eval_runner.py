"""Eval run consumer — the job queue for long-running eval batches
(plan/evaluation.md §3).

Design notes worth keeping in mind before changing anything here:

- **The item snapshot is the ground.** At run start the dataset's current item
  IDs are frozen into `eval_runs.item_ids`; the run iterates that list, so
  editing the dataset mid-run cannot shift what a run measured (checklist
  row 9). Comparison in Phase 6 depends on this.
- **Resume is idempotent, not clever.** The unique `(run_id, item_id)` index
  makes per-item work replayable: on resume we skip items that already have a
  result row. A killed runner therefore costs at most one item's work.
- **`updated_at` is the heartbeat.** A run whose heartbeat has gone stale is
  presumed orphaned and reclaimed by another worker. That is the only liveness
  signal — there is no separate lease table.
- **A grader error is an item-level outcome.** It never fails the run, and the
  item is excluded from *that grader's* mean rather than scored zero
  (plan/evaluation.md §3's aggregation rule) — otherwise a broken grader would
  silently look like a quality regression.
"""

import asyncio
import json
import logging
import random
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert

from .config import get_settings
from .db import transaction
from .graders import GradeContext, load_graders, run_grader
from .metrics import EVAL_ITEMS, EVAL_RUNS
from .providers import build_chat_model
from .registry import resolve_price
from .streams import EVAL_JOBS_STREAM, EVAL_RUNNERS_GROUP
from .tables import dataset_items, eval_results, eval_runs

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- target generation ------------------------------------------------------


def _fill_template(prompt: str, item_input: Any) -> str:
    """`{{var}}` placeholders filled from the item's input.

    A missing variable is left as-is rather than raising: a half-filled prompt
    is visible in the results table, while a crashed run explains nothing.
    """
    if not isinstance(item_input, dict):
        return prompt.replace("{{input}}", _as_text(item_input))
    out = prompt
    for key, value in item_input.items():
        out = out.replace("{{" + str(key) + "}}", _as_text(value))
    return out.replace("{{input}}", _as_text(item_input))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


async def _generate(target: dict, item: dict, model_factory: Any) -> tuple[Any, int | None, int | None]:
    """Produces the output to grade. Returns (output, tokens_in, tokens_out).

    Retries provider 429/overload with jittered backoff (checklist row 10);
    after the last attempt the exception propagates and the item records an
    error while the run continues.
    """
    model_name = target.get("model")
    prompt = _fill_template(target.get("prompt") or "{{input}}", item["input"])

    if model_factory is not None:
        model = model_factory(model_name)
    else:
        # Provider resolves from the model name (ADR-0016); a missing key
        # raises and becomes this item's error, not the run's.
        model = build_chat_model(model_name, max_tokens=target.get("max_tokens") or 2048)

    last: Exception | None = None
    for attempt in range(4):
        try:
            reply = await model.ainvoke(prompt)
            break
        except Exception as exc:
            last = exc
            text = f"{type(exc).__name__}: {exc}".lower()
            retryable = any(
                s in text for s in ("rate limit", "overloaded", "timeout", "too many requests", "529")
            )
            if not retryable or attempt == 3:
                raise
            await asyncio.sleep(min(2**attempt, 60) * (0.5 + random.random() / 2))
    else:  # pragma: no cover
        raise last or RuntimeError("target generation failed")

    output = getattr(reply, "content", reply)
    usage = getattr(reply, "usage_metadata", None) or {}
    return output, usage.get("input_tokens"), usage.get("output_tokens")


# ---- one item ---------------------------------------------------------------


async def _run_item(run: dict, item: dict, model_factory: Any) -> dict:
    """Scores one item. Never raises — failures become the row's `error`."""
    started = time.perf_counter()
    target = run["target_config"] or {}
    graders = (run["grader_config"] or {}).get("graders", [])

    output: Any = item["expected_output"]
    tokens_in = tokens_out = None
    error: str | None = None

    if target.get("mode", "generate") == "generate":
        try:
            output, tokens_in, tokens_out = await _generate(target, item, model_factory)
        except Exception as exc:
            log.warning("target generation failed for item %s: %s", item["id"], exc)
            error = f"{type(exc).__name__}: {exc}"[:1000]

    scores: dict[str, Any] = {}
    reasoning: dict[str, str] = {}
    judge_cost = 0.0

    if error is None:
        meta = item.get("metadata") or {}
        context = GradeContext(
            input=item["input"],
            expected_output=item["expected_output"],
            chunks=meta.get("chunks"),
            metadata=meta,
            model_factory=model_factory,
        )
        for spec in graders:
            name = spec.get("name", "")
            result, grader_error = await run_grader(name, output, context, spec.get("config") or {})
            if grader_error is not None:
                # Item-level error: excluded from this grader's mean, never 0.
                scores[name] = {"error": grader_error}
                continue
            scores[name] = {"score": result.score, "detail": result.detail}
            if result.reasoning:
                reasoning[name] = result.reasoning
            # Judging is billable work; a run that hides it under-reports spend.
            if result.tokens_in is not None and result.tokens_out is not None:
                judge_price = resolve_price(result.model)
                if judge_price:
                    this_grader = (result.tokens_in / 1_000_000) * judge_price[0] + (
                        result.tokens_out / 1_000_000
                    ) * judge_price[1]
                    judge_cost += this_grader
                    # Per-grader, so an expensive judge is identifiable.
                    scores[name]["cost_usd"] = round(this_grader, 8)

    # Item cost = target generation + every judge that scored it.
    price = resolve_price(target.get("model"))
    cost = None
    if price and tokens_in is not None and tokens_out is not None:
        cost = (tokens_in / 1_000_000) * price[0] + (tokens_out / 1_000_000) * price[1]
    if judge_cost > 0:
        cost = (cost or 0.0) + judge_cost

    return {
        "id": uuid.uuid4(),
        "run_id": run["id"],
        "item_id": item["id"],
        "output": {"value": output} if not isinstance(output, (dict, list)) else output,
        "scores": scores or None,
        "judge_reasoning": reasoning or None,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "cost_usd": cost,
        "error": error,
        "created_at": _now(),
    }


# ---- summary ----------------------------------------------------------------


def summarize(rows: list[dict], graders: list[dict]) -> dict:
    """Per-grader mean and pass-rate, plus run totals.

    The aggregation rule lives here: an item whose grader errored is excluded
    from that grader's mean and counted in `errored_items`, so degraded
    coverage is visible rather than disguised as a low score.
    """
    per_grader: dict[str, Any] = {}
    for spec in graders:
        name = spec.get("name", "")
        threshold = float((spec.get("config") or {}).get("threshold", 0.5))
        values, errored = [], 0
        for row in rows:
            entry = (row.get("scores") or {}).get(name)
            if entry is None:
                continue
            if "error" in entry:
                errored += 1
                continue
            values.append(float(entry.get("score", 0.0)))
        per_grader[name] = {
            "mean": (sum(values) / len(values)) if values else None,
            "pass_rate": (sum(1 for v in values if v >= threshold) / len(values)) if values else None,
            "scored_items": len(values),
            "errored_items": errored,
        }

    latencies = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    costs = [float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None]
    failed = sum(1 for r in rows if r.get("error"))

    return {
        "graders": per_grader,
        "total_items": len(rows),
        "failed_items": failed,
        "total_cost_usd": sum(costs) if costs else 0.0,
        "mean_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "p95_latency_ms": latencies[int(len(latencies) * 0.95)] if latencies else None,
    }


# ---- the runner -------------------------------------------------------------


class EvalRunner:
    """Consumes `eval:jobs` and executes runs, resuming orphans on the way."""

    def __init__(self, redis: Redis, model_factory: Any = None) -> None:
        self.redis = redis
        self.model_factory = model_factory  # tests inject fake LLMs here
        self.consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def _ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(EVAL_JOBS_STREAM, EVAL_RUNNERS_GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self) -> None:
        load_graders()
        await self._ensure_group()
        log.info("eval runner %s reading %s", self.consumer, EVAL_JOBS_STREAM)

        last_reclaim = 0.0
        while not self._stop:
            settings = get_settings()
            now = time.monotonic()
            if now - last_reclaim > settings.eval_reclaim_interval_s:
                last_reclaim = now
                await self._resume_orphans()

            try:
                batch = await self.redis.xreadgroup(
                    EVAL_RUNNERS_GROUP, self.consumer, {EVAL_JOBS_STREAM: ">"}, count=1, block=2000
                )
            except RedisError:
                log.exception("eval job read failed; backing off")
                await asyncio.sleep(1)
                continue

            for _stream, messages in batch or []:
                for message_id, fields in messages:
                    try:
                        await self.execute(uuid.UUID(fields["run_id"]))
                    except Exception:
                        log.exception("eval run %s failed", fields.get("run_id"))
                    finally:
                        # Ack regardless: a failed run is recorded as failed in
                        # Postgres, and redelivering it would re-run the work.
                        await self.redis.xack(EVAL_JOBS_STREAM, EVAL_RUNNERS_GROUP, message_id)

    async def _resume_orphans(self) -> None:
        """Picks up runs whose heartbeat went stale — a killed runner's work."""
        settings = get_settings()
        cutoff = _now() - timedelta(seconds=settings.eval_stale_run_s)
        async with transaction() as conn:
            rows = await conn.execute(
                select(eval_runs.c.id).where(
                    and_(eval_runs.c.status == "running", eval_runs.c.updated_at < cutoff)
                )
            )
            orphans = [r.id for r in rows]
        for run_id in orphans:
            log.info("resuming orphaned eval run %s", run_id)
            try:
                await self.execute(run_id)
            except Exception:
                log.exception("resume of run %s failed", run_id)

    async def execute(self, run_id: uuid.UUID) -> None:
        run = await self._claim(run_id)
        if run is None:
            return

        items = await self._load_items(run)
        done = await self._existing_results(run_id)
        pending = [i for i in items if i["id"] not in done]

        settings = get_settings()
        semaphore = asyncio.Semaphore(settings.eval_item_concurrency)
        heartbeat = asyncio.create_task(self._heartbeat(run_id))

        try:
            for chunk_start in range(0, len(pending), settings.eval_item_concurrency):
                if await self._is_cancelled(run_id):
                    log.info("eval run %s cancelled; stopping", run_id)
                    heartbeat.cancel()
                    return

                chunk = pending[chunk_start : chunk_start + settings.eval_item_concurrency]

                async def guarded(item: dict) -> dict:
                    async with semaphore:
                        return await _run_item(run, item, self.model_factory)

                rows = await asyncio.gather(*(guarded(i) for i in chunk))
                await self._persist(run_id, rows)
                EVAL_ITEMS.inc(len(rows))
        finally:
            heartbeat.cancel()

        await self._complete(run_id, run)

    async def _claim(self, run_id: uuid.UUID) -> dict | None:
        """Marks the run running and freezes its item snapshot.

        Returns None when another worker already owns it or it is no longer
        runnable — both are normal, not errors.
        """
        async with transaction() as conn:
            row = (
                await conn.execute(select(eval_runs).where(eval_runs.c.id == run_id))
            ).mappings().one_or_none()
            if row is None:
                log.warning("eval run %s not found", run_id)
                return None
            if row["status"] not in ACTIVE:
                return None

            run = dict(row)
            if not run.get("item_ids"):
                item_rows = await conn.execute(
                    select(dataset_items.c.id)
                    .where(dataset_items.c.dataset_id == run["dataset_id"])
                    .order_by(dataset_items.c.id)
                )
                run["item_ids"] = [str(r.id) for r in item_rows]

            await conn.execute(
                update(eval_runs)
                .where(eval_runs.c.id == run_id)
                .values(
                    status="running",
                    item_ids=run["item_ids"],
                    total=len(run["item_ids"]),
                    started_at=run["started_at"] or _now(),
                    updated_at=_now(),
                )
            )
            return run

    async def _load_items(self, run: dict) -> list[dict]:
        """Loads the snapshot's items, in snapshot order."""
        ids = [uuid.UUID(i) for i in (run["item_ids"] or [])]
        if not ids:
            return []
        async with transaction() as conn:
            rows = await conn.execute(select(dataset_items).where(dataset_items.c.id.in_(ids)))
            by_id = {r["id"]: dict(r) for r in rows.mappings()}
        return [by_id[i] for i in ids if i in by_id]

    async def _existing_results(self, run_id: uuid.UUID) -> set[uuid.UUID]:
        async with transaction() as conn:
            rows = await conn.execute(
                select(eval_results.c.item_id).where(eval_results.c.run_id == run_id)
            )
            return {r.item_id for r in rows}

    async def _persist(self, run_id: uuid.UUID, rows: list[dict]) -> None:
        if not rows:
            return
        async with transaction() as conn:
            # The unique (run_id, item_id) index makes a redelivered chunk a
            # no-op rather than a duplicate.
            await conn.execute(
                insert(eval_results).values(rows).on_conflict_do_nothing(
                    index_elements=["run_id", "item_id"]
                )
            )
            # Progress is recounted rather than incremented: a resumed run must
            # report what actually landed, not what this process persisted.
            count = await conn.scalar(
                select(func.count())
                .select_from(eval_results)
                .where(eval_results.c.run_id == run_id)
            )
            await conn.execute(
                update(eval_runs)
                .where(eval_runs.c.id == run_id)
                .values(progress=count or 0, updated_at=_now())
            )

    async def _is_cancelled(self, run_id: uuid.UUID) -> bool:
        async with transaction() as conn:
            status = await conn.scalar(select(eval_runs.c.status).where(eval_runs.c.id == run_id))
        return status == "cancelled"

    async def _heartbeat(self, run_id: uuid.UUID) -> None:
        """Keeps `updated_at` fresh so other workers don't reclaim this run."""
        interval = get_settings().eval_heartbeat_s
        try:
            while True:
                await asyncio.sleep(interval)
                async with transaction() as conn:
                    await conn.execute(
                        update(eval_runs).where(eval_runs.c.id == run_id).values(updated_at=_now())
                    )
        except asyncio.CancelledError:
            pass

    async def _complete(self, run_id: uuid.UUID, run: dict) -> None:
        async with transaction() as conn:
            status = await conn.scalar(select(eval_runs.c.status).where(eval_runs.c.id == run_id))
            if status == "cancelled":
                return

            rows = [
                dict(r)
                for r in (
                    await conn.execute(select(eval_results).where(eval_results.c.run_id == run_id))
                ).mappings()
            ]
            summary = summarize(rows, (run["grader_config"] or {}).get("graders", []))
            await conn.execute(
                update(eval_runs)
                .where(eval_runs.c.id == run_id)
                .values(
                    status="completed",
                    progress=len(rows),
                    summary=summary,
                    completed_at=_now(),
                    updated_at=_now(),
                )
            )
        EVAL_RUNS.labels(status="completed").inc()
        log.info("eval run %s completed: %d items", run_id, len(rows))
