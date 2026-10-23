"""§26.1's Jenkins row (``OPEN-127``): a live probe, never a raise, never Jenkins's words."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.deployments.status import jenkins_row
from app.metrics import service
from app.metrics.client import FAILURE_THRESHOLD
from tests.deployments.harness import TOKEN, Harness


async def test_unconfigured_is_not_configured_and_says_which_setting() -> None:
    get_settings.cache_clear()
    row = await jenkins_row()
    assert (row.name, row.type, row.status) == ("Jenkins", "ci", "not_configured")
    assert row.last_error_message == "DCP_JENKINS_URL is not set."
    assert row.circuit_state is None or row.circuit_state == "closed"


async def test_a_reachable_jenkins_is_ok_with_a_closed_circuit(harness: Harness) -> None:
    row = await jenkins_row(harness.client)
    assert row.status == "ok" and row.circuit_state == "closed"
    assert row.last_success_at is not None and row.last_error_message is None
    assert row.demo_mode is True  # the test environment is in demonstration mode


async def test_an_outage_is_unavailable_with_a_closed_reason_and_never_raises(
    harness: Harness,
) -> None:
    harness.mock.mode = "outage"
    row = await jenkins_row(harness.client)
    assert row.status == "unavailable" and row.last_error_message == "provider_error"
    assert row.last_error_at is not None


async def test_an_open_circuit_says_so(harness: Harness) -> None:
    harness.mock.mode = "outage"
    for _ in range(FAILURE_THRESHOLD):
        await jenkins_row(harness.client)
    row = await jenkins_row(harness.client)
    assert row.status == "unavailable" and row.last_error_message == "circuit_open"
    assert row.circuit_state == "open"


async def test_a_refused_credential_is_unavailable_auth_failed(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DCPSECRET_JENKINS_TOKEN", "wrong")
    row = await jenkins_row(harness.client)
    assert row.status == "unavailable" and row.last_error_message == "auth_failed"


async def test_no_row_carries_a_url_a_token_or_jenkins_words(harness: Harness) -> None:
    rows = []
    harness.mock.mode = "outage"
    rows.append(await jenkins_row(harness.client))
    harness.mock.mode = "ok"
    harness.circuit.reset()
    rows.append(await jenkins_row(harness.client))
    for row in rows:
        dump: Any = row.model_dump_json()
        assert "://" not in dump and TOKEN not in dump and "mock-jenkins" not in dump


async def test_the_page_lists_jenkins_once_in_the_contract_position(harness: Harness) -> None:
    page = await service.integration_status()
    names = [row.name for row in page.integrations]
    assert names.count("Jenkins") == 1
    assert names.index("Alertmanager") < names.index("Jenkins") < names.index("Secret store")
