"""Alert rule evaluation and delivery (plan/evaluation.md §6).

Every 60 s each enabled rule computes its metric over a trailing window and
compares it to a threshold. A breach opens an `alert_events` row; recovery
closes it.

The two rules that make alerting bearable rather than noisy:

- **No re-fire while unresolved.** A rule that is breaching stays breaching, and
  emitting an event every minute for an hour is how people end up writing mail
  filters for their alerts. One open event per rule at a time, closed when the
  metric comes back.
- **Delivery cannot lose an alert.** The event row is written and committed
  *before* any channel is called. A webhook that is down leaves
  `notified = false` — visible, retryable, and still on the dashboard — rather
  than an alert that never existed.

Metrics come from two places: `error_rate`, `cost` and `latency_p95` are SQL
over `traces`; `eval_score` is the mean over `monitor_scores`, which is what
connects the sampler to alerting.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select, update

from .channels import deliver
from .db import transaction
from .metrics import ALERT_DELIVERY_FAILURES, ALERTS_FIRED, ALERTS_RESOLVED
from .tables import alert_events, alert_rules, monitor_configs, monitor_scores, projects, traces

log = logging.getLogger(__name__)

TICK_S = 60

METRICS = ("error_rate", "cost", "latency_p95", "eval_score")
COMPARISONS = ("gt", "lt", "gte", "lte")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def breaches(value: float | None, comparison: str, threshold: float) -> bool:
    """Whether `value` breaches the rule.

    `None` is never a breach. No data is not the same as good data, but firing
    on it would mean every new project alerts immediately, and an alert that
    fires before anything has happened teaches people to ignore it.
    """
    if value is None:
        return False
    if comparison == "gt":
        return value > threshold
    if comparison == "gte":
        return value >= threshold
    if comparison == "lt":
        return value < threshold
    if comparison == "lte":
        return value <= threshold
    log.warning("unknown comparison %r; treating as no breach", comparison)
    return False


async def measure(rule: dict, now: datetime | None = None) -> float | None:
    """Computes the rule's metric over its trailing window. None when no data."""
    now = now or _now()
    since = now - timedelta(minutes=int(rule["window_minutes"]))
    metric = rule["metric"]

    async with transaction() as conn:
        if metric == "eval_score":
            # The sampler's output, averaged across every grader on every
            # sampled trace in the window — a rule about "quality", not about
            # one named grader.
            project_monitors = select(monitor_configs.c.id).where(
                monitor_configs.c.project_id == rule["project_id"]
            )
            rows = await conn.execute(
                select(monitor_scores.c.scores).where(
                    monitor_scores.c.created_at >= since,
                    monitor_scores.c.monitor_id.in_(project_monitors),
                )
            )
            values: list[float] = []
            for (scores,) in rows:
                for entry in (scores or {}).values():
                    # A grader error is an absence, not a zero — the same rule
                    # the eval aggregation uses. Averaging errors in as zeros
                    # would fire an alert about a broken judge and label it a
                    # quality regression.
                    if (
                        isinstance(entry, dict)
                        and "error" not in entry
                        and entry.get("score") is not None
                    ):
                        values.append(float(entry["score"]))
            return (sum(values) / len(values)) if values else None

        window = and_(traces.c.project_id == rule["project_id"], traces.c.started_at >= since)

        if metric == "error_rate":
            row = (
                await conn.execute(
                    select(
                        func.count().label("total"),
                        func.sum(case((traces.c.status == "error", 1), else_=0)).label("errors"),
                    ).where(window)
                )
            ).one()
            total = row.total or 0
            return (float(row.errors or 0) / total) if total else None

        if metric == "cost":
            # Total spend in the window, not a mean — "$40 in an hour" is the
            # alertable thing, and a mean hides a volume spike entirely.
            value = await conn.scalar(select(func.sum(traces.c.total_cost_usd)).where(window))
            return float(value) if value is not None else None

        if metric == "latency_p95":
            value = await conn.scalar(
                select(
                    func.percentile_cont(0.95).within_group(traces.c.latency_ms.asc())
                ).where(and_(window, traces.c.latency_ms.isnot(None)))
            )
            return float(value) if value is not None else None

    log.warning("unknown metric %r on rule %s", metric, rule["id"])
    return None


async def open_event(rule: dict) -> dict | None:
    """The rule's currently-unresolved event, if any."""
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    select(alert_events)
                    .where(
                        alert_events.c.rule_id == rule["id"],
                        alert_events.c.resolved_at.is_(None),
                    )
                    .order_by(alert_events.c.fired_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row else None


async def channel_config(project_id: uuid.UUID) -> dict[str, dict]:
    """What channels this project's alerts go to.

    Only the built-in webhook for now, and only when a URL is set — a project
    with none configured still gets the event on the dashboard, which is the
    always-on channel.
    """
    async with transaction() as conn:
        url = await conn.scalar(
            select(projects.c.alert_webhook_url).where(projects.c.id == project_id)
        )
    return {"webhook": {"url": url}} if url else {}


async def evaluate(rule: dict, now: datetime | None = None) -> str | None:
    """Evaluates one rule. Returns "fired", "resolved", or None."""
    value = await measure(rule, now)
    existing = await open_event(rule)
    breaching = breaches(value, rule["comparison"], float(rule["threshold"]))

    if breaching and existing is None:
        event = {
            "id": uuid.uuid4(),
            "rule_id": rule["id"],
            "fired_at": _now(),
            "value": value,
            "resolved_at": None,
            "notified": False,
        }
        # Committed before delivery is attempted: an alert that fired but could
        # not be delivered is still an alert that fired.
        async with transaction() as conn:
            await conn.execute(alert_events.insert().values(**event))

        delivered = await deliver(event, rule, await channel_config(rule["project_id"]))
        if delivered:
            async with transaction() as conn:
                await conn.execute(
                    update(alert_events)
                    .where(alert_events.c.id == event["id"])
                    .values(notified=True)
                )
        else:
            ALERT_DELIVERY_FAILURES.inc()

        ALERTS_FIRED.labels(metric=rule["metric"]).inc()
        log.warning(
            "alert fired: %s (%s %s %s, value %s)",
            rule["name"],
            rule["metric"],
            rule["comparison"],
            rule["threshold"],
            value,
        )
        return "fired"

    if not breaching and existing is not None:
        resolved_at = _now()
        async with transaction() as conn:
            await conn.execute(
                update(alert_events)
                .where(alert_events.c.id == existing["id"])
                .values(resolved_at=resolved_at)
            )
        await deliver(
            {**existing, "resolved_at": resolved_at, "value": value if value is not None else existing["value"]},
            rule,
            await channel_config(rule["project_id"]),
        )
        ALERTS_RESOLVED.labels(metric=rule["metric"]).inc()
        log.info("alert resolved: %s (value %s)", rule["name"], value)
        return "resolved"

    return None


class Alerter:
    """The 60-second loop."""

    def __init__(self) -> None:
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def tick(self) -> dict[str, int]:
        counts = {"fired": 0, "resolved": 0}
        async with transaction() as conn:
            rows = await conn.execute(select(alert_rules).where(alert_rules.c.enabled.is_(True)))
            rules = [dict(r) for r in rows.mappings()]

        for rule in rules:
            try:
                outcome = await evaluate(rule)
            except Exception:
                # A rule with a bad metric or a slow query must not stop the
                # others from being evaluated.
                log.exception("alert rule %s failed to evaluate", rule.get("name"))
                continue
            if outcome:
                counts[outcome] += 1
        return counts

    async def run(self) -> None:
        log.info("alerter up (tick %ss)", TICK_S)
        while not self._stop:
            try:
                await self.tick()
            except Exception:
                log.exception("alerter tick failed")
            await asyncio.sleep(TICK_S)
