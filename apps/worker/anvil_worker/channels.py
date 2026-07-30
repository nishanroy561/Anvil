"""Alert channels (plan/plugins.md §1, plan/evaluation.md §6).

Third registry alongside graders and pricing, on the same contract: built-ins
register through the mechanism external packages use, which is what keeps the
contract honest. The webhook below is the built-in reference implementation.

Two invariants (plugins.md §3):

- **A failing channel never blocks the others.** Each `send` is wrapped in its
  own try/except and timeout; one channel raising or hanging must not stop an
  alert reaching a second channel, and must not stop the *next* alert either.
- **Delivery failure is recorded, not raised.** `alert_events.notified` stays
  false, which is a visible degraded state rather than a lost alert. The event
  itself is already persisted before any channel is called — an alert that
  fired but could not be delivered is still an alert that fired.
"""

import asyncio
import logging
from typing import Protocol

from .config import get_settings

log = logging.getLogger(__name__)

CHANNELS_ENTRY_POINT = "anvil.alert_channels"

# Channels talk to the network, so the budget is tighter than a grader's; a
# webhook that hangs must not hold the alerter's cycle open (plugins.md §3).
DEFAULT_TIMEOUT_S = 10.0


class AlertChannel(Protocol):
    name: str

    async def send(self, event: dict, rule: dict, config: dict) -> None: ...


class WebhookChannel:
    """POSTs the event as JSON to `projects.alert_webhook_url`.

    Deliberately unopinionated about the payload shape — a flat JSON object any
    receiver can read, rather than a vendor format. A Slack-shaped channel is
    the example plugin in Phase 11, which is the point of the registry.
    """

    name = "webhook"

    async def send(self, event: dict, rule: dict, config: dict) -> None:
        url = (config or {}).get("url")
        if not url:
            # Not an error: a project with no webhook configured is the common
            # case, and the dashboard bell is always there.
            log.debug("webhook channel: no url configured for rule %s", rule.get("id"))
            return

        import httpx

        payload = {
            "event": "alert.fired" if event.get("resolved_at") is None else "alert.resolved",
            "rule": {
                "id": str(rule.get("id")),
                "name": rule.get("name"),
                "metric": rule.get("metric"),
                "comparison": rule.get("comparison"),
                "threshold": float(rule["threshold"]) if rule.get("threshold") is not None else None,
                "window_minutes": rule.get("window_minutes"),
            },
            "value": float(event["value"]) if event.get("value") is not None else None,
            "fired_at": event["fired_at"].isoformat() if event.get("fired_at") else None,
            "resolved_at": event["resolved_at"].isoformat() if event.get("resolved_at") else None,
            "project_id": str(rule.get("project_id")),
        }
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()


BUILTINS = (WebhookChannel(),)

_channels: dict[str, AlertChannel] = {}


def register_channel(channel: AlertChannel) -> bool:
    if channel.name in _channels:
        log.error("alert channel %r already registered; ignoring duplicate", channel.name)
        return False
    _channels[channel.name] = channel
    return True


def load_channels() -> None:
    _channels.clear()
    for channel in BUILTINS:
        register_channel(channel)

    if get_settings().deployment_mode == "cloud":
        log.info("cloud mode: skipping external alert channels (ADR-0012)")
        return

    try:
        from importlib.metadata import entry_points

        for entry_point in entry_points(group=CHANNELS_ENTRY_POINT):
            try:
                register_channel(entry_point.load()())
                log.info("loaded alert channel plugin %r", entry_point.name)
            except Exception:
                log.exception("alert channel %r failed to load; skipping", entry_point.name)
    except Exception:
        log.exception("alert channel entry-point discovery failed; using built-ins only")


def registered_names() -> list[str]:
    if not _channels:
        load_channels()
    return sorted(_channels)


def get_channel(name: str) -> AlertChannel | None:
    """Mirrors `graders.get_grader`. Both registries lazily load on first access,
    so a caller never has to know whether startup has run yet."""
    if not _channels:
        load_channels()
    return _channels.get(name)


async def deliver(event: dict, rule: dict, configs: dict[str, dict]) -> bool:
    """Sends one event to every configured channel. Never raises.

    Returns True only if **every** attempted channel succeeded — that is what
    `alert_events.notified` records, so a partial delivery reads as degraded
    rather than complete.
    """
    if not _channels:
        load_channels()

    delivered = True
    for name, config in (configs or {}).items():
        channel = _channels.get(name)
        if channel is None:
            log.warning("alert channel %r is not registered; skipping", name)
            delivered = False
            continue
        try:
            await asyncio.wait_for(channel.send(event, rule, config), timeout=DEFAULT_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("alert channel %r timed out after %ss", name, DEFAULT_TIMEOUT_S)
            delivered = False
        except Exception:
            # One broken channel must not cost the alert its other deliveries.
            log.exception("alert channel %r failed", name)
            delivered = False
    return delivered
