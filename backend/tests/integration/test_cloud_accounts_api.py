"""§26.7 end to end: what the adapter for a cloud account can do (``OPEN-61``, ``OPEN-64``).

Authority is narrowed live and the answer watched narrowing. The "secret" is a credential
reference stored on the account, which must appear in no response.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.providers import registry
from tests.integration.inventory_helpers import account_id, call, disable_account
from tests.integration.test_alerts_api import Rig
from tests.integration.test_alerts_api import rig as rig
from tests.integration.test_auth_flow import (  # noqa: F401 -- fixtures are used by name
    FakeIdp,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)
from tests.providers.scripted import ScriptedAdapter

ROLE_ARN = "arn:aws:iam::123456789012:role/dcp-canary-credential"
ACTIONS = {"start", "stop", "restart", "status", "logs_tail"}


def path(account: object) -> str:
    return f"/api/v1/cloud-accounts/{account}/capabilities"


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DCP_DEMO_MODE", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("DCP_DEMO_MODE")
    get_settings.cache_clear()


@pytest.fixture
def not_demo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The suite's environment may have demonstration mode on; these tests need it off."""
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("DCP_DEMO_MODE")
    get_settings.cache_clear()


@pytest.fixture
def account(migrated: Engine) -> Iterator[uuid.UUID]:
    """An account of its own, carrying a credential reference that must never be echoed."""
    identifier = account_id(migrated)
    with migrated.begin() as connection:
        connection.execute(
            text("UPDATE cloud_accounts SET credential_reference = :r WHERE id = :i"),
            {"r": ROLE_ARN, "i": identifier},
        )
    yield identifier
    with migrated.begin() as connection:
        connection.execute(
            text(
                "UPDATE cloud_accounts SET credential_reference = NULL, status = 'ACTIVE',"
                " deleted_at = NULL WHERE id = :i"
            ),
            {"i": identifier},
        )


def denials(rig: Rig) -> int:
    with rig.engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE action_type = 'AUTHZ_DENIED'"
                    " AND actor_id = :a"
                ),
                {"a": rig.caller},
            ).scalar_one()
        )


async def test_with_no_adapter_the_answer_is_200_with_unknown_capabilities(
    rig: Rig, account: uuid.UUID, not_demo: None
) -> None:
    response = await call(rig.client, "GET", path(account))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adapter_state"] == "not_configured"
    assert body["adapter_id"] is None
    assert body["capabilities"] is None  # unknown is not "supports nothing"
    assert body["data_state"] == "not_configured"
    assert body["reason"] == "no_adapter_for_provider"
    assert body["demo_mode"] is False
    assert body["account_id"] == str(account) and body["account_status"] == "ACTIVE"


async def test_in_demonstration_mode_every_capability_is_not_configured(
    rig: Rig, account: uuid.UUID, demo: None
) -> None:
    body = (await call(rig.client, "GET", path(account))).json()
    assert body["adapter_state"] == "demonstration" and body["adapter_id"] == "demo"
    assert body["demo_mode"] is True and body["data_state"] == "ok" and body["reason"] is None
    capabilities = body["capabilities"]
    assert set(capabilities["actions"]) == ACTIONS
    assert set(capabilities["actions"].values()) == {"not_configured"}
    assert capabilities["inventory_sync"] == capabilities["connectivity_check"] == "not_configured"
    assert capabilities["async_handle"] is False and capabilities["concurrency"] == "none"


async def test_a_registered_adapter_is_reported_as_configured_not_as_verified(
    rig: Rig, account: uuid.UUID, not_demo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = (
        rig.engine.connect()
        .execute(text("SELECT provider FROM cloud_accounts WHERE id = :i"), {"i": account})
        .scalar_one()
    )
    monkeypatch.setitem(registry._FACTORIES, provider, ScriptedAdapter)
    body = (await call(rig.client, "GET", path(account))).json()
    assert body["adapter_state"] == "configured" and body["adapter_id"] == "scripted"
    assert body["demo_mode"] is False and body["data_state"] == "ok"
    assert set(body["capabilities"]["actions"].values()) == {"supported"}


async def test_a_disabled_account_is_reported_not_hidden(rig: Rig, account: uuid.UUID) -> None:
    disable_account(rig.engine, account)
    response = await call(rig.client, "GET", path(account))
    assert response.status_code == 200 and response.json()["account_status"] == "DISABLED"


async def test_unknown_and_soft_deleted_accounts_are_404(rig: Rig, account: uuid.UUID) -> None:
    assert (await call(rig.client, "GET", path(uuid.uuid4()))).status_code == 404
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE cloud_accounts SET deleted_at = now() WHERE id = :i"), {"i": account}
        )
    assert (await call(rig.client, "GET", path(account))).status_code == 404


async def test_a_malformed_id_and_an_unknown_parameter_are_422(
    rig: Rig, account: uuid.UUID
) -> None:
    assert (await call(rig.client, "GET", path("not-a-uuid"))).status_code == 422
    response = await call(rig.client, "GET", path(account), params={"provider": "AWS"})
    assert response.status_code == 422


@pytest.mark.parametrize("role", ["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER"])
async def test_only_an_administrator_may_read_it_and_the_refusal_is_audited(
    rig: Rig, account: uuid.UUID, role: str
) -> None:
    rig.narrow(role)
    before = denials(rig)
    response = await call(rig.client, "GET", path(account))
    assert response.status_code == 403
    assert "capabilities" not in response.text
    assert denials(rig) == before + 1


async def test_a_denied_caller_learns_nothing_about_whether_the_account_exists(
    rig: Rig, account: uuid.UUID
) -> None:
    rig.narrow("VIEWER")
    real = await call(rig.client, "GET", path(account))
    absent = await call(rig.client, "GET", path(uuid.uuid4()))
    assert real.status_code == absent.status_code == 403


async def test_the_response_carries_no_credential_and_only_the_documented_keys(
    rig: Rig, account: uuid.UUID, demo: None
) -> None:
    response = await call(rig.client, "GET", path(account))
    assert ROLE_ARN not in response.text and "credential" not in response.text.lower()
    assert set(response.json()) == {
        "account_id", "provider", "account_status", "adapter_state", "adapter_id",
        "capabilities", "data_state", "reason", "demo_mode",
    }  # fmt: skip
    assert json.loads(response.text)["capabilities"].keys() == {
        "inventory_sync", "connectivity_check", "actions", "async_handle", "concurrency", "latency",
    }  # fmt: skip


async def test_a_read_writes_no_audit_row(rig: Rig, account: uuid.UUID) -> None:
    def count() -> int:
        with rig.engine.connect() as connection:
            return int(connection.execute(text("SELECT count(*) FROM audit_events")).scalar_one())

    before = count()
    assert (await call(rig.client, "GET", path(account))).status_code == 200
    assert count() == before
