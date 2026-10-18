"""Scope arithmetic and the Alertmanager status row, without a database (OPEN-45, OPEN-47)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.alerts import counts, liveness
from app.alerts.queries import unconstrained
from app.alerts.status import WATCHDOG_STALE_SECONDS, alertmanager_status
from app.authz.matcher import Grant
from app.core.config import get_settings

NOW = datetime.now(UTC)


def allow(scope: dict[str, Any], *, resource: str = "alert", action: str = "read") -> Grant:
    return Grant(resource_type=resource, action=action, effect="ALLOW", scope=scope)


FULL = {
    "environment": ["development", "staging", "production", "other"],
    "cloud_account": ["*"],
    "server": ["*"],
    "application": ["*"],
}


# --- who may see an alert about no server -----------------------------------------------------


@pytest.mark.parametrize(
    ("grants", "expected"),
    [
        ([allow(FULL)], True),  # the bootstrap administrator's shape
        ([allow({"environment": ["*"], "server": ["*"]})], True),
        ([allow({"environment": ["staging"], "cloud_account": ["*"]})], False),
        ([allow({"environment": ["production"]})], False),
        ([allow({**FULL, "server": ["0192f3a1-7c1e-7b4f-9a55-1d2c3e4f5a6b"]})], False),
        ([allow({**FULL, "cloud_account": ["0192f3a1-7c1e-7b4f-9a55-1d2c3e4f5a6b"]})], False),
        ([allow({"environment": ["development", "staging", "production"]})], False),  # not 'other'
        ([allow({})], False),  # grants nothing, readable_servers agrees
        ([allow({"nonsense": ["*"]})], False),  # malformed
        ([allow(FULL, resource="server")], False),  # a different permission
        ([], False),
        (
            [allow(FULL), Grant("alert", "read", "DENY", {"environment": ["production"]})],
            False,  # with a deny in play it cannot say what is excluded
        ),
    ],
)
def test_only_a_grant_that_constrains_nothing_sees_an_alert_about_no_server(
    grants: list[Grant], expected: bool
) -> None:
    assert unconstrained(grants, "alert", "read") is expected


def test_one_unconstrained_grant_among_narrow_ones_is_enough() -> None:
    narrow = allow({"environment": ["staging"]})
    assert unconstrained([narrow, allow(FULL)], "alert", "read")


def test_a_caller_without_alert_read_cannot_read_alerts() -> None:
    assert not counts.can_read([])
    assert not counts.can_read([allow(FULL, action="acknowledge")])
    assert counts.can_read([allow(FULL)])


# --- the integration row --------------------------------------------------------------------------


def transport(status: int = 200, *, error: Exception | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/-/ready"
        if error is not None:
            raise error
        return httpx.Response(status)

    return httpx.MockTransport(handler)


def facts(watchdog: datetime | None) -> dict[str, object]:
    return {"last_delivery_at": watchdog, "last_watchdog_at": watchdog, "refused_today": {}}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_ALERTMANAGER_URL", "http://alertmanager.invalid:9093")
    get_settings.cache_clear()


def stub_liveness(monkeypatch: pytest.MonkeyPatch, value: dict[str, object] | None) -> None:
    async def read() -> dict[str, object] | None:
        return value

    monkeypatch.setattr(liveness, "read", read)


async def test_unconfigured_says_so() -> None:
    get_settings.cache_clear()
    row = await alertmanager_status()
    assert (row.name, row.status) == ("Alertmanager", "not_configured")
    assert row.last_error_message == "DCP_ALERTMANAGER_URL is not set."


async def test_ok_needs_both_a_ready_alertmanager_and_a_recent_watchdog(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_liveness(monkeypatch, facts(NOW - timedelta(minutes=5)))
    row = await alertmanager_status(transport(), now=NOW)
    assert row.status == "ok" and row.last_error_message is None
    assert row.last_success_at == NOW - timedelta(minutes=5)  # the proof, not the probe


@pytest.mark.parametrize(
    ("watchdog", "reason"),
    [
        (None, "watchdog_never_seen"),
        (NOW - timedelta(seconds=WATCHDOG_STALE_SECONDS + 60), "watchdog_stale"),
    ],
)
async def test_a_ready_alertmanager_nobody_has_heard_from_is_not_ok(
    configured: None, monkeypatch: pytest.MonkeyPatch, watchdog: datetime | None, reason: str
) -> None:
    """A process that answers is not proof that alerts flow (PRD §3)."""
    stub_liveness(monkeypatch, facts(watchdog))
    row = await alertmanager_status(transport(), now=NOW)
    assert (row.status, row.last_error_message) == ("unavailable", reason)


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (transport(503), "alertmanager_not_ready"),
        (transport(302), "alertmanager_not_ready"),  # a redirect is not followed
        (transport(error=httpx.ConnectError("refused")), "alertmanager_unreachable"),
        (transport(error=httpx.ReadTimeout("slow")), "alertmanager_timeout"),
    ],
)
async def test_each_probe_failure_has_its_own_closed_reason_and_no_url(
    configured: None, monkeypatch: pytest.MonkeyPatch, kind: httpx.MockTransport, reason: str
) -> None:
    stub_liveness(monkeypatch, facts(NOW))
    row = await alertmanager_status(kind, now=NOW)
    assert (row.status, row.last_error_message) == ("unavailable", reason)
    assert "://" not in row.last_error_message


async def test_an_unreadable_liveness_store_is_unknown_not_fine(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_liveness(monkeypatch, None)
    row = await alertmanager_status(transport(), now=NOW)
    assert (row.status, row.last_error_message) == ("unavailable", "alert_liveness_unreadable")


# --- the dashboard pipeline field ---


async def test_pipeline_state(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    assert await counts.pipeline_state() is None  # no Alertmanager configured

    monkeypatch.setenv("DCP_ALERTMANAGER_URL", "http://alertmanager.invalid:9093")
    get_settings.cache_clear()
    stub_liveness(monkeypatch, facts(NOW - timedelta(minutes=1)))
    assert await counts.pipeline_state(NOW) == "confirmed"
    stub_liveness(monkeypatch, facts(None))
    assert await counts.pipeline_state(NOW) == "not_confirmed"
    stub_liveness(monkeypatch, facts(NOW - timedelta(seconds=WATCHDOG_STALE_SECONDS + 1)))
    assert await counts.pipeline_state(NOW) == "not_confirmed"
    stub_liveness(monkeypatch, None)
    assert await counts.pipeline_state(NOW) == "unknown"
