"""Phase-1 acceptance: auth brute-force limits (edge-case checklist row 17).

Per-IP and per-email fixed windows (ADR-0007), and the fail-open guarantee —
a rate limiter must never become the outage.
"""

import pytest
from redis.exceptions import RedisError

from anvil_api.config import get_settings
from anvil_api.metrics import RATELIMIT_REJECTIONS
from anvil_api.services import rate_limit

from .conftest import requires_services

pytestmark = requires_services


@pytest.fixture(autouse=True)
def frozen_window(monkeypatch):
    """Pin the fixed window so a real clock rollover can't flake these tests."""
    monkeypatch.setattr(rate_limit.time, "time", lambda: 1_700_000_000.0)


def _login(client, email: str):
    return client.post("/v1/auth/login", json={"email": email, "password": "wrong-password"})


async def test_per_ip_limit_blocks_the_11th_attempt_and_spares_other_ips(client_for_ip):
    limit = get_settings().auth_ip_limit_pm
    assert limit == 10

    async with client_for_ip("203.0.113.1") as attacker:
        # Distinct emails each time, so the (lower) per-email limit isn't what trips.
        for i in range(limit):
            assert (await _login(attacker, f"victim{i}@example.com")).status_code == 401

        blocked = await _login(attacker, f"victim{limit}@example.com")
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "rate_limited"
        assert int(blocked.headers["Retry-After"]) > 0

    # A different IP is unaffected: the limit is per-IP, not global.
    async with client_for_ip("203.0.113.99") as bystander:
        assert (await _login(bystander, "someone@example.com")).status_code == 401


async def test_per_email_limit_blocks_across_ips(client_for_ip):
    limit = get_settings().auth_email_limit_pm
    assert limit == 5
    target = "target@example.com"

    # Rotate IPs so the per-IP limit stays well clear — only the email is shared.
    for i in range(limit):
        async with client_for_ip(f"198.51.100.{i}") as c:
            assert (await _login(c, target)).status_code == 401

    async with client_for_ip("198.51.100.200") as c:
        assert (await _login(c, target)).status_code == 429

    # Another email from that same fresh IP still gets through.
    async with client_for_ip("198.51.100.200") as c:
        assert (await _login(c, "other@example.com")).status_code == 401


async def test_rejections_are_exported_to_prometheus(client, client_for_ip):
    """Self-observability ships with the component it measures (plan.md)."""
    before = RATELIMIT_REJECTIONS.labels(scope="auth:email")._value.get()

    async with client_for_ip("198.51.100.50") as c:
        for _ in range(get_settings().auth_email_limit_pm + 1):
            await _login(c, "metered@example.com")

    assert RATELIMIT_REJECTIONS.labels(scope="auth:email")._value.get() > before

    body = (await client.get("/metrics")).text
    assert 'anvil_ratelimit_rejections_total{scope="auth:email"}' in body


async def test_limiter_fails_open_when_redis_is_down(client_for_ip, monkeypatch):
    class DeadRedis:
        async def incrby(self, key, amount):
            raise RedisError("connection refused")

        async def expire(self, key, ttl):
            raise RedisError("connection refused")

    monkeypatch.setattr("anvil_api.routers.auth.get_redis", lambda: DeadRedis())

    # Well past both limits: with Redis unavailable every request must still be
    # served (401 for bad credentials), never 429.
    async with client_for_ip("192.0.2.7") as c:
        for _ in range(get_settings().auth_ip_limit_pm + 5):
            assert (await _login(c, "target@example.com")).status_code == 401
