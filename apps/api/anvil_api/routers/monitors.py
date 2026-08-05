"""Monitor and alert-rule APIs (plan/evaluation.md §6).

The worker owns evaluation; this owns configuration and reading back what
happened. Both credentials work (ADR-0021) — a deploy pipeline may well want to
disable a monitor before a load test.

Cron strings are validated here rather than discovered to be broken by a worker
at 3am: a monitor with an unparseable schedule silently never runs, which is
indistinguishable from one that runs and finds nothing wrong.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller, current_caller
from ..db import get_session
from ..deps import NOT_FOUND, owned_project
from ..models import AlertEvent, AlertRule, MonitorConfig, MonitorScore
from ..schemas.monitoring import (
    AlertEventOut,
    AlertRuleCreate,
    AlertRuleOut,
    AlertRuleUpdate,
    MonitorCreate,
    MonitorOut,
    MonitorScoreOut,
    MonitorScoreSeries,
    MonitorUpdate,
    ScoreBucket,
    WebhookUpdate,
)
from ..services.graders_catalog import builtin_names, validate_grader_config

router = APIRouter(prefix="/v1", tags=["monitors"])
log = logging.getLogger(__name__)


def validate_cron(expression: str) -> None:
    """Refuse a schedule the sampler could never act on.

    The import failure used to `return`, silently accepting anything. croniter was
    a *worker* dependency, so in dev — where both services' requirements share an
    environment — validation ran and the test below passed, while in the API image
    it was skipped entirely. That is the worst possible split: the check appears
    to work everywhere it is tested and is absent everywhere it is deployed.

    croniter is now a declared dependency of this service, so the fallback should
    be unreachable. It refuses rather than accepts, because the harm being
    prevented is a monitor that never runs and looks healthy doing it — and
    creating one we could not validate is exactly that harm.
    """
    try:
        from croniter import croniter
    except ImportError as exc:
        log.error("croniter is missing from the API image; cannot validate cron expressions")
        raise HTTPException(
            500,
            detail={
                "code": "cron_validation_unavailable",
                "message": (
                    "The server cannot validate cron expressions right now, so it "
                    "will not create a monitor that might never run."
                ),
            },
        ) from exc
    if not croniter.is_valid(expression):
        raise HTTPException(
            400,
            detail={
                "code": "invalid_cron",
                "message": (
                    f"{expression!r} is not a valid cron expression. A monitor with an "
                    "unparseable schedule would never run and would look healthy doing it."
                ),
            },
        )


def validate_graders(graders) -> None:
    for grader in graders:
        if grader.name not in builtin_names():
            continue  # may be a worker-side plugin the API can't see
        problem = validate_grader_config(grader.name, grader.config)
        if problem:
            raise HTTPException(
                400,
                detail={
                    "code": "invalid_grader_config",
                    "message": f"Config for grader '{grader.name}' is invalid — {problem}",
                },
            )


async def owned_monitor(monitor_id: uuid.UUID, caller: Caller, session: AsyncSession) -> MonitorConfig:
    monitor = await session.get(MonitorConfig, monitor_id)
    if monitor is None:
        raise NOT_FOUND
    await owned_project(monitor.project_id, caller, session)
    return monitor


async def owned_rule(rule_id: uuid.UUID, caller: Caller, session: AsyncSession) -> AlertRule:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise NOT_FOUND
    await owned_project(rule.project_id, caller, session)
    return rule


# ---- monitors ---------------------------------------------------------------


@router.post("/projects/{project_id}/monitors", response_model=MonitorOut, status_code=201)
async def create_monitor(
    project_id: uuid.UUID,
    body: MonitorCreate,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, caller, session)
    validate_cron(body.schedule_cron)
    validate_graders(body.graders)

    monitor = MonitorConfig(
        project_id=project.id,
        name=body.name,
        sample_rate=body.sample_rate,
        schedule_cron=body.schedule_cron,
        grader_config={"graders": [g.model_dump() for g in body.graders]},
        enabled=body.enabled,
    )
    session.add(monitor)
    await session.commit()
    await session.refresh(monitor)
    return MonitorOut.model_validate(monitor)


@router.get("/projects/{project_id}/monitors", response_model=list[MonitorOut])
async def list_monitors(
    project_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, caller, session)
    rows = await session.execute(
        select(MonitorConfig)
        .where(MonitorConfig.project_id == project.id)
        .order_by(MonitorConfig.created_at.desc())
    )
    return [MonitorOut.model_validate(m) for m in rows.scalars()]


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
async def get_monitor(
    monitor_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    return MonitorOut.model_validate(await owned_monitor(monitor_id, caller, session))


@router.patch("/monitors/{monitor_id}", response_model=MonitorOut)
async def update_monitor(
    monitor_id: uuid.UUID,
    body: MonitorUpdate,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    monitor = await owned_monitor(monitor_id, caller, session)
    fields = body.model_dump(exclude_unset=True)

    if "schedule_cron" in fields and fields["schedule_cron"] is not None:
        validate_cron(fields["schedule_cron"])
        monitor.schedule_cron = fields["schedule_cron"]
    if "graders" in fields and body.graders is not None:
        validate_graders(body.graders)
        monitor.grader_config = {"graders": [g.model_dump() for g in body.graders]}
    for field in ("name", "sample_rate", "enabled"):
        if field in fields and fields[field] is not None:
            setattr(monitor, field, fields[field])

    await session.commit()
    await session.refresh(monitor)
    return MonitorOut.model_validate(monitor)


@router.delete("/monitors/{monitor_id}", status_code=204)
async def delete_monitor(
    monitor_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    monitor = await owned_monitor(monitor_id, caller, session)
    # Scores reference the monitor, so they go first — losing the history is
    # the accepted consequence of deleting the monitor that produced it.
    await session.execute(
        MonitorScore.__table__.delete().where(MonitorScore.monitor_id == monitor.id)
    )
    await session.delete(monitor)
    await session.commit()


@router.get("/monitors/{monitor_id}/scores", response_model=MonitorScoreSeries)
async def monitor_score_series(
    monitor_id: uuid.UUID,
    days: int = Query(14, ge=1, le=90),
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    """Daily mean score for the drift chart.

    Averaged over every grader on every sample in the bucket, with grader
    errors excluded rather than counted as zero — the same aggregation rule the
    eval summary and the `eval_score` alert metric use. A broken judge must not
    look like a quality collapse.
    """
    monitor = await owned_monitor(monitor_id, caller, session)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = await session.execute(
        select(MonitorScore.created_at, MonitorScore.scores)
        .where(MonitorScore.monitor_id == monitor.id, MonitorScore.created_at >= since)
        .order_by(MonitorScore.created_at)
    )

    buckets: dict[datetime, list[float]] = {}
    graders: list[str] = []
    for created_at, scores in rows:
        day = created_at.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for name, entry in (scores or {}).items():
            if name not in graders:
                graders.append(name)
            if isinstance(entry, dict) and "error" not in entry and entry.get("score") is not None:
                buckets.setdefault(day, []).append(float(entry["score"]))
        buckets.setdefault(day, buckets.get(day, []))

    series = [
        ScoreBucket(
            bucket=day,
            mean_score=(sum(values) / len(values)) if values else None,
            samples=len(values),
        )
        for day, values in sorted(buckets.items())
    ]
    return MonitorScoreSeries(buckets=series, graders=sorted(graders))


@router.get("/monitors/{monitor_id}/samples", response_model=list[MonitorScoreOut])
async def monitor_samples(
    monitor_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    """Most recent samples, newest first — the list the UI links to traces."""
    monitor = await owned_monitor(monitor_id, caller, session)
    rows = await session.execute(
        select(MonitorScore)
        .where(MonitorScore.monitor_id == monitor.id)
        .order_by(MonitorScore.created_at.desc())
        .limit(limit)
    )
    return [MonitorScoreOut.model_validate(s) for s in rows.scalars()]


# ---- alert rules ------------------------------------------------------------


@router.post("/projects/{project_id}/alert-rules", response_model=AlertRuleOut, status_code=201)
async def create_alert_rule(
    project_id: uuid.UUID,
    body: AlertRuleCreate,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, caller, session)
    rule = AlertRule(project_id=project.id, **body.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return AlertRuleOut.model_validate(rule)


@router.get("/projects/{project_id}/alert-rules", response_model=list[AlertRuleOut])
async def list_alert_rules(
    project_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, caller, session)
    rows = await session.execute(
        select(AlertRule)
        .where(AlertRule.project_id == project.id)
        .order_by(AlertRule.created_at.desc())
    )
    return [AlertRuleOut.model_validate(r) for r in rows.scalars()]


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    rule_id: uuid.UUID,
    body: AlertRuleUpdate,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    rule = await owned_rule(rule_id, caller, session)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return AlertRuleOut.model_validate(rule)


@router.delete("/alert-rules/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    rule = await owned_rule(rule_id, caller, session)
    await session.execute(AlertEvent.__table__.delete().where(AlertEvent.rule_id == rule.id))
    await session.delete(rule)
    await session.commit()


# ---- alert events -----------------------------------------------------------


@router.get("/projects/{project_id}/alert-events", response_model=list[AlertEventOut])
async def list_alert_events(
    project_id: uuid.UUID,
    active: bool = Query(False, description="Only events that are still unresolved"),
    limit: int = Query(50, ge=1, le=200),
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    """Event history for the project, newest first.

    Joined to the rule so the UI can say *what* fired without a second call —
    an event id on its own tells a reader nothing.
    """
    project = await owned_project(project_id, caller, session)
    query = (
        select(AlertEvent, AlertRule.name, AlertRule.metric)
        .join(AlertRule, AlertRule.id == AlertEvent.rule_id)
        .where(AlertRule.project_id == project.id)
        .order_by(AlertEvent.fired_at.desc())
        .limit(limit)
    )
    if active:
        query = query.where(AlertEvent.resolved_at.is_(None))

    rows = await session.execute(query)
    return [
        AlertEventOut(
            **{
                **AlertEventOut.model_validate(event).model_dump(),
                "rule_name": rule_name,
                "metric": metric,
            }
        )
        for event, rule_name, metric in rows
    ]


@router.get("/projects/{project_id}/alert-events/count")
async def active_alert_count(
    project_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    """Drives the dashboard bell — the always-on channel."""
    project = await owned_project(project_id, caller, session)
    count = await session.scalar(
        select(func.count())
        .select_from(AlertEvent)
        .join(AlertRule, AlertRule.id == AlertEvent.rule_id)
        .where(AlertRule.project_id == project.id, AlertEvent.resolved_at.is_(None))
    )
    return {"active": count or 0}


# ---- webhook config ---------------------------------------------------------


@router.patch("/projects/{project_id}/alert-webhook", response_model=WebhookUpdate)
async def set_alert_webhook(
    project_id: uuid.UUID,
    body: WebhookUpdate,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, caller, session)
    url = body.alert_webhook_url
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(
            400,
            detail={"code": "invalid_webhook_url", "message": "The URL must start with http:// or https://."},
        )
    project.alert_webhook_url = url or None
    await session.commit()
    return WebhookUpdate(alert_webhook_url=project.alert_webhook_url)


@router.get("/projects/{project_id}/alert-webhook", response_model=WebhookUpdate)
async def get_alert_webhook(
    project_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
):
    project = await owned_project(project_id, caller, session)
    return WebhookUpdate(alert_webhook_url=project.alert_webhook_url)
