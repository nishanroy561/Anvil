"""`slack` — the same alert as the built-in webhook, in Slack's Block Kit shape.

This is the case the channel registry exists for. Anvil's built-in webhook sends
a flat, vendor-neutral JSON object deliberately: guessing at one vendor's schema
would be wrong for everyone else. Slack wants `blocks`, PagerDuty wants an event
action, Opsgenie wants something else again — so the *shape* is the plugin's job.

The contract (plan/plugins.md §1):

- a `name` attribute
- `async def send(event, rule, config) -> None`

Raise on failure. Anvil catches it, leaves `alert_events.notified` false, and
counts a delivery failure — a degraded, visible state rather than a lost alert.
Do **not** swallow errors to look successful: that turns a dead webhook into an
alert nobody knows was never delivered.

One channel raising or hanging never blocks another, and never blocks the next
alert: the worker wraps every `send` in its own timeout and try/except. Which
means a plugin does not need to be defensive on Anvil's behalf — only correct.
"""

from typing import Any

# Slack renders at most 50 blocks and truncates long text, so the message is
# built small rather than dumping the event.
MAX_TEXT = 300


def _emoji(fired: bool) -> str:
    return ":rotating_light:" if fired else ":white_check_mark:"


def _sentence(rule: dict, value: Any, fired: bool) -> str:
    """Plain language, because the person reading this is on their phone.

    "eval_score lt 0.5" is a rule definition; "quality is below 0.5 (now 0.29)"
    is what happened.
    """
    metric = str(rule.get("metric") or "metric").replace("_", " ")
    comparison = {"lt": "below", "gt": "above", "lte": "at or below", "gte": "at or above"}.get(
        str(rule.get("comparison")), str(rule.get("comparison") or "past")
    )
    threshold = rule.get("threshold")
    window = rule.get("window_minutes")

    if not fired:
        return f"{metric} is back within its threshold ({threshold})."

    now = f" (now {value:.3f})" if isinstance(value, (int, float)) else ""
    over = f" over the last {window} min" if window else ""
    return f"{metric} is {comparison} {threshold}{now}{over}."


class SlackChannel:
    name = "slack"

    async def send(self, event: dict, rule: dict, config: dict) -> None:
        url = (config or {}).get("url")
        if not url:
            # A project with no Slack URL configured is not an error — the same
            # convention the built-in webhook follows.
            return

        fired = event.get("resolved_at") is None
        title = f"{_emoji(fired)} {'Alert' if fired else 'Resolved'}: {rule.get('name') or 'Anvil alert'}"

        payload = {
            # `text` is the notification preview and the accessibility fallback.
            # Blocks alone show as an empty message in some Slack surfaces.
            "text": f"{title} — {_sentence(rule, event.get('value'), fired)}"[:MAX_TEXT],
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{title}*"},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _sentence(rule, event.get("value"), fired)[:MAX_TEXT],
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"project `{rule.get('project_id')}`",
                        }
                    ],
                },
            ],
        }

        # Imported here, not at module scope: an import that fails at load time
        # takes the whole plugin out of the registry, while one that fails here
        # costs a single delivery and is recorded as such.
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            # Slack answers 200 with the body "invalid_payload" for a malformed
            # message, so status alone is not success.
            response.raise_for_status()
            if response.text.strip() not in ("ok", ""):
                raise RuntimeError(f"slack rejected the message: {response.text[:200]}")
