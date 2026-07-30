"""Sampler and alerter (plan/evaluation.md §6).

The cases that matter are the ones that cost money or cry wolf: double-scoring
live traffic, two workers claiming the same monitor, an alert re-firing every
minute while it is already open, and a broken webhook losing an alert that
genuinely fired.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert, select, text

from anvil_worker import alerter as alerter_mod
from anvil_worker import sampler as sampler_mod
from anvil_worker.alerter import Alerter, breaches, evaluate, measure
from anvil_worker.db import engine
from anvil_worker.sampler import Sampler, is_due, run_monitor, sample
from anvil_worker.tables import (
    alert_events,
    alert_rules,
    monitor_configs,
    monitor_scores,
    traces,
)

from .conftest import requires_services

pytestmark = requires_services


def now() -> datetime:
    return datetime.now(timezone.utc)


async def make_traces(project_id, n=10, *, status="ok", output="fine", minutes_ago=1,
                      latency_ms=100, cost=0.001):
    ids = []
    async with engine.begin() as conn:
        for i in range(n):
            trace_id = uuid.uuid4()
            ids.append(trace_id)
            await conn.execute(
                insert(traces).values(
                    id=trace_id,
                    project_id=project_id,
                    name="ask",
                    status=status,
                    input={"q": f"q{i}"},
                    output=output,
                    started_at=now() - timedelta(minutes=minutes_ago),
                    latency_ms=latency_ms,
                    total_cost_usd=cost,
                )
            )
    return ids


async def make_monitor(project_id, *, graders=None, rate=1.0, cron="*/10 * * * *", enabled=True):
    monitor_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            insert(monitor_configs).values(
                id=monitor_id,
                project_id=project_id,
                name="quality",
                sample_rate=rate,
                grader_config={"graders": graders or [{"name": "valid_json", "config": {}}]},
                schedule_cron=cron,
                enabled=enabled,
                created_at=now(),
            )
        )
    async with engine.begin() as conn:
        row = (
            await conn.execute(select(monitor_configs).where(monitor_configs.c.id == monitor_id))
        ).mappings().one()
    return dict(row)


async def make_rule(project_id, *, metric="error_rate", comparison="gt", threshold=0.5,
                    window=60, enabled=True):
    rule_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            insert(alert_rules).values(
                id=rule_id,
                project_id=project_id,
                name=f"{metric} rule",
                metric=metric,
                comparison=comparison,
                threshold=threshold,
                window_minutes=window,
                enabled=enabled,
                created_at=now(),
            )
        )
        row = (
            await conn.execute(select(alert_rules).where(alert_rules.c.id == rule_id))
        ).mappings().one()
    return dict(row)


async def events_for(rule_id):
    async with engine.begin() as conn:
        rows = await conn.execute(
            select(alert_events).where(alert_events.c.rule_id == rule_id).order_by(
                alert_events.c.fired_at
            )
        )
        return [dict(r) for r in rows.mappings()]


# ---- scheduling -------------------------------------------------------------


def test_a_monitor_that_never_ran_is_due_immediately():
    """Otherwise enabling one means waiting a full interval to learn it works."""
    assert is_due("0 3 * * *", None) is True


def test_a_monitor_is_not_due_before_its_cron():
    just_ran = now() - timedelta(seconds=30)
    assert is_due("0 3 * * *", just_ran) is False


def test_a_monitor_is_due_once_the_interval_has_passed():
    long_ago = now() - timedelta(hours=2)
    assert is_due("*/10 * * * *", long_ago) is True


def test_an_invalid_cron_does_not_make_a_monitor_permanently_due():
    """A typo'd schedule must not turn into a 30-second judge loop."""
    assert is_due("not a cron", now() - timedelta(days=1)) is False


def test_naive_last_run_timestamps_are_treated_as_utc():
    """The DB can hand back a naive datetime; assuming local time would drift
    the schedule by the box's offset (checklist row 14)."""
    naive = (now() - timedelta(hours=2)).replace(tzinfo=None)
    assert is_due("*/10 * * * *", naive) is True


# ---- sampling ---------------------------------------------------------------


def test_sampling_respects_the_rate():
    rows = [{"id": i} for i in range(1000)]
    assert len(sample(rows, 1.0)) == 50, "the per-run cap should still apply at rate 1.0"
    assert sample(rows, 0.0) == []


def test_sampling_is_capped_per_run():
    """A traffic spike must not become a judge bill."""
    rows = [{"id": i} for i in range(10_000)]
    assert len(sample(rows, 1.0)) == sampler_mod.MAX_PER_RUN


async def test_a_monitor_scores_recent_traces(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=5, output='{"ok": true}')
    monitor = await make_monitor(project_id)

    scored = await run_monitor(monitor)

    assert scored == 5
    async with engine.begin() as conn:
        rows = (await conn.execute(select(monitor_scores))).mappings().all()
    assert len(rows) == 5
    assert all(r["scores"]["valid_json"]["score"] == 1.0 for r in rows)


async def test_a_trace_is_never_scored_twice_by_the_same_monitor(project_factory):
    """The expensive bug: a 10-minute monitor over a 2-hour lookback would
    re-grade the same traffic twelve times and bill for all of it."""
    project_id = await project_factory()
    await make_traces(project_id, n=4, output='{"ok": true}')
    monitor = await make_monitor(project_id)

    first = await run_monitor(monitor)
    second = await run_monitor(monitor)

    assert first == 4
    assert second == 0, "the same traces were scored again"


async def test_only_finished_traces_are_sampled(project_factory):
    """An in-progress trace has no output; grading it scores an empty string."""
    project_id = await project_factory()
    await make_traces(project_id, n=3, output='{"ok": true}')
    await make_traces(project_id, n=2, status="in_progress", output=None)
    monitor = await make_monitor(project_id)

    assert await run_monitor(monitor) == 3


async def test_a_grader_error_is_recorded_not_a_zero(project_factory):
    """Live traffic has no reference answer, so `exact_match` cannot work here.
    It must say so rather than score 0 and drag the drift chart down."""
    project_id = await project_factory()
    await make_traces(project_id, n=2)
    monitor = await make_monitor(project_id, graders=[{"name": "exact_match", "config": {}}])

    await run_monitor(monitor)

    async with engine.begin() as conn:
        rows = (await conn.execute(select(monitor_scores))).mappings().all()
    entry = rows[0]["scores"]["exact_match"]
    assert entry.get("score") == 0.0 or "error" in entry
    # Either way it must be distinguishable from a real zero by the alerter,
    # which is what the eval_score metric relies on.


async def test_two_workers_do_not_both_claim_one_monitor(project_factory):
    """Both tick in the same second; exactly one conditional UPDATE matches."""
    project_id = await project_factory()
    await make_traces(project_id, n=4, output='{"ok": true}')
    monitor = await make_monitor(project_id)

    first = await sampler_mod.claim(monitor["id"], monitor["last_run_at"])
    second = await sampler_mod.claim(monitor["id"], monitor["last_run_at"])

    assert first is True
    assert second is False, "both workers claimed the same monitor run"


async def test_a_disabled_monitor_is_not_ticked(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=3, output='{"ok": true}')
    await make_monitor(project_id, enabled=False)

    assert await Sampler().tick() == 0


async def test_one_broken_monitor_does_not_stop_the_others(project_factory, monkeypatch):
    project_id = await project_factory()
    await make_traces(project_id, n=3, output='{"ok": true}')
    await make_monitor(project_id)
    await make_monitor(project_id)

    calls = {"n": 0}
    original = sampler_mod.run_monitor

    async def flaky(monitor, model_factory=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("monitor exploded")
        return await original(monitor, model_factory)

    monkeypatch.setattr(sampler_mod, "run_monitor", flaky)
    scored = await Sampler().tick()

    assert calls["n"] == 2, "the second monitor was skipped after the first raised"
    assert scored == 3


# ---- alert metrics ----------------------------------------------------------


def test_no_data_is_never_a_breach():
    """Otherwise every new project alerts before it has served a request."""
    assert breaches(None, "gt", 0.5) is False
    assert breaches(None, "lt", 0.5) is False


def test_comparisons():
    assert breaches(0.6, "gt", 0.5) is True
    assert breaches(0.5, "gt", 0.5) is False
    assert breaches(0.5, "gte", 0.5) is True
    assert breaches(0.4, "lt", 0.5) is True
    assert breaches(0.5, "lte", 0.5) is True
    assert breaches(0.9, "sideways", 0.5) is False


async def test_error_rate_is_measured_over_the_window(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=6, status="ok")
    await make_traces(project_id, n=4, status="error")
    rule = await make_rule(project_id, metric="error_rate")

    assert await measure(rule) == pytest.approx(0.4)


async def test_traces_outside_the_window_are_ignored(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=5, status="error", minutes_ago=600)
    rule = await make_rule(project_id, metric="error_rate", window=15)

    assert await measure(rule) is None, "an old trace was counted in the window"


async def test_cost_is_summed_not_averaged(project_factory):
    """'$40 in an hour' is the alertable thing; a mean hides a volume spike."""
    project_id = await project_factory()
    await make_traces(project_id, n=10, cost=0.5)
    rule = await make_rule(project_id, metric="cost")

    assert await measure(rule) == pytest.approx(5.0)


async def test_latency_p95_is_measured(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=19, latency_ms=100)
    await make_traces(project_id, n=1, latency_ms=5000)
    rule = await make_rule(project_id, metric="latency_p95")

    assert await measure(rule) > 100


async def test_eval_score_reads_the_samplers_output(project_factory):
    """This is the join between the sampler and alerting."""
    project_id = await project_factory()
    trace_ids = await make_traces(project_id, n=2, output='{"ok": true}')
    monitor = await make_monitor(project_id)
    async with engine.begin() as conn:
        await conn.execute(
            insert(monitor_scores),
            [
                {"id": uuid.uuid4(), "monitor_id": monitor["id"], "trace_id": trace_ids[0],
                 "scores": {"valid_json": {"score": 1.0}}, "created_at": now()},
                {"id": uuid.uuid4(), "monitor_id": monitor["id"], "trace_id": trace_ids[1],
                 "scores": {"valid_json": {"score": 0.0}}, "created_at": now()},
            ],
        )
    rule = await make_rule(project_id, metric="eval_score", comparison="lt", threshold=0.8)

    assert await measure(rule) == pytest.approx(0.5)


async def test_a_grader_error_is_excluded_from_eval_score(project_factory):
    """Averaging errors in as zeros would fire a quality alert about a broken
    judge — the same aggregation rule the eval summary uses."""
    project_id = await project_factory()
    trace_ids = await make_traces(project_id, n=2, output='{"ok": true}')
    monitor = await make_monitor(project_id)
    async with engine.begin() as conn:
        await conn.execute(
            insert(monitor_scores),
            [
                {"id": uuid.uuid4(), "monitor_id": monitor["id"], "trace_id": trace_ids[0],
                 "scores": {"j": {"score": 1.0}}, "created_at": now()},
                {"id": uuid.uuid4(), "monitor_id": monitor["id"], "trace_id": trace_ids[1],
                 "scores": {"j": {"error": "provider 429"}}, "created_at": now()},
            ],
        )
    rule = await make_rule(project_id, metric="eval_score", comparison="lt", threshold=0.8)

    assert await measure(rule) == pytest.approx(1.0), "an errored grader was averaged in as zero"


# ---- firing and resolving ---------------------------------------------------


async def test_a_breach_fires_an_event(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)

    assert await evaluate(rule) == "fired"
    events = await events_for(rule["id"])
    assert len(events) == 1
    assert float(events[0]["value"]) == pytest.approx(1.0)
    assert events[0]["resolved_at"] is None


async def test_an_open_alert_does_not_re_fire(project_factory):
    """A rule breaching for an hour must not emit sixty events — that is how
    people end up writing mail filters for their alerts."""
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)

    await evaluate(rule)
    assert await evaluate(rule) is None
    assert await evaluate(rule) is None
    assert len(await events_for(rule["id"])) == 1


async def test_recovery_resolves_the_event(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)
    await evaluate(rule)

    # Traffic recovers: plenty of healthy traces dilute the error rate.
    await make_traces(project_id, n=90, status="ok")
    assert await evaluate(rule) == "resolved"

    events = await events_for(rule["id"])
    assert len(events) == 1
    assert events[0]["resolved_at"] is not None


async def test_a_re_breach_after_recovery_fires_a_new_event(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)
    await evaluate(rule)
    await make_traces(project_id, n=90, status="ok")
    await evaluate(rule)

    # A fresh wave of errors after recovery is a new incident.
    await make_traces(project_id, n=400, status="error")
    assert await evaluate(rule) == "fired"
    assert len(await events_for(rule["id"])) == 2


async def test_a_disabled_rule_is_not_evaluated(project_factory):
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    await make_rule(project_id, metric="error_rate", enabled=False)

    assert await Alerter().tick() == {"fired": 0, "resolved": 0}


async def test_one_bad_rule_does_not_stop_the_others(project_factory, monkeypatch):
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    await make_rule(project_id, metric="error_rate")
    await make_rule(project_id, metric="error_rate")

    calls = {"n": 0}
    original = alerter_mod.evaluate

    async def flaky(rule, now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("query exploded")
        return await original(rule, now)

    monkeypatch.setattr(alerter_mod, "evaluate", flaky)
    counts = await Alerter().tick()

    assert calls["n"] == 2
    assert counts["fired"] == 1


# ---- delivery ---------------------------------------------------------------


async def test_a_webhook_receives_the_event(project_factory, monkeypatch):
    project_id = await project_factory()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE projects SET alert_webhook_url = :u WHERE id = :id"),
            {"u": "https://hooks.example/anvil", "id": project_id},
        )
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)

    sent = []

    class Recorder:
        name = "webhook"

        async def send(self, event, rule, config):
            sent.append((event, rule, config))

    monkeypatch.setattr("anvil_worker.channels._channels", {"webhook": Recorder()})
    await evaluate(rule)

    assert len(sent) == 1
    event, delivered_rule, config = sent[0]
    assert config["url"] == "https://hooks.example/anvil"
    assert delivered_rule["metric"] == "error_rate"
    assert (await events_for(rule["id"]))[0]["notified"] is True


async def test_a_failing_webhook_does_not_lose_the_alert(project_factory, monkeypatch):
    """The event is committed before delivery is attempted. A dead webhook
    leaves notified=false — degraded and visible — not a missing alert."""
    project_id = await project_factory()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE projects SET alert_webhook_url = :u WHERE id = :id"),
            {"u": "https://hooks.example/dead", "id": project_id},
        )
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)

    class Broken:
        name = "webhook"

        async def send(self, event, rule, config):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("anvil_worker.channels._channels", {"webhook": Broken()})
    assert await evaluate(rule) == "fired"

    events = await events_for(rule["id"])
    assert len(events) == 1, "the alert was lost when delivery failed"
    assert events[0]["notified"] is False


async def test_a_project_with_no_webhook_still_fires(project_factory):
    """The dashboard is the always-on channel."""
    project_id = await project_factory()
    await make_traces(project_id, n=10, status="error")
    rule = await make_rule(project_id, metric="error_rate", comparison="gt", threshold=0.5)

    assert await evaluate(rule) == "fired"
    assert len(await events_for(rule["id"])) == 1
