"""The demonstration adapter (``OPEN-63``) and the registry (``OPEN-62``)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.providers import registry
from app.providers.base import ProviderAdapter
from app.providers.capabilities import Concurrency, Support
from app.providers.demo import DemoAdapter
from app.providers.errors import NotConfigured, ProviderError
from app.providers.types import ExecutionHandle, ServiceAction
from tests.providers.conformance import SERVER, SERVICE
from tests.providers.scripted import ScriptedAdapter

ARCHITECTURE = Path(__file__).parents[3] / "docs" / "ARCHITECTURE.md"
PROVIDERS = ["AWS", "AZURE", "ON_PREM", "OTHER", "GCP", ""]


# --- the demonstration adapter ---------------------------------------------------------------


def test_every_result_it_returns_is_flagged_demo() -> None:
    result = DemoAdapter().get_server_info(SERVER)
    assert result.demo_mode is True
    assert result.adapter_id == "demo"
    assert result.data_state == "ok"


def test_server_info_only_echoes_what_the_caller_held() -> None:
    info = DemoAdapter().get_server_info(SERVER).value
    assert info is not None
    assert (info.provider_resource_id, info.region) == (SERVER.provider_resource_id, SERVER.region)
    assert info.provider_status == "demonstration"


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.check_connectivity(SERVER),
        lambda a: a.list_managed_services(SERVER, SERVICE),
        lambda a: a.get_operation_status(SERVER, ExecutionHandle("demo", "x", datetime.now(UTC))),
    ],
    ids=["connectivity", "services", "status"],
)
def test_it_never_asserts_the_condition_of_a_host(call: Any) -> None:
    with pytest.raises(NotConfigured) as caught:
        call(DemoAdapter())
    assert caught.value.detail_code == "demonstration_mode"
    assert caught.value.adapter_id == "demo"


@pytest.mark.parametrize("action", list(ServiceAction))
def test_every_write_is_not_configured_and_issues_no_handle(action: ServiceAction) -> None:
    adapter = DemoAdapter()
    with pytest.raises(NotConfigured):
        adapter.execute_operation(SERVER, SERVICE, action, operation_id=uuid.uuid4())


def test_a_weakened_gate_still_could_not_make_it_a_writer() -> None:
    # Bypass the base-class gate on purpose: _submit refuses by itself.
    with pytest.raises(NotConfigured):
        DemoAdapter()._submit(SERVER, SERVICE, ServiceAction.RESTART, operation_id=uuid.uuid4())


def test_capabilities_are_not_configured_not_unsupported() -> None:
    caps = DemoAdapter().capabilities()
    assert caps.inventory_sync is Support.NOT_CONFIGURED
    assert caps.connectivity_check is Support.NOT_CONFIGURED
    assert set(caps.actions.values()) == {Support.NOT_CONFIGURED}
    assert caps.async_handle is False
    assert caps.concurrency is Concurrency.NONE


def test_the_demo_column_of_the_documented_matrix_is_what_the_code_says() -> None:
    """ARCHITECTURE.md §7.2 is a contract (TESTING §6): a change to either side fails here."""
    rows = {
        line.split("|")[1].strip(): line.split("|")[-2].strip().strip("`")
        for line in ARCHITECTURE.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ")
    }
    caps = DemoAdapter().capabilities()
    assert rows["Inventory sync"] == caps.inventory_sync.value
    assert rows["Connectivity check"] == caps.connectivity_check.value
    assert rows["Allowlisted service control"] == Support.NOT_CONFIGURED.value
    assert set(caps.actions.values()) == {Support.NOT_CONFIGURED}
    assert rows["Async handle + polling"] == "No handle is ever issued"
    assert caps.async_handle is False
    assert rows["Concurrent invocation limits"].lower() == caps.concurrency.value


# --- the registry ----------------------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_demo_mode_serves_the_demo_adapter_for_any_provider(provider: str) -> None:
    assert isinstance(registry.resolve_adapter(provider, demo_mode=True), DemoAdapter)


@pytest.mark.parametrize("provider", [p for p in PROVIDERS if p != "AWS"])
def test_without_demo_mode_nothing_is_registered_so_it_fails_closed(provider: str) -> None:
    with pytest.raises(NotConfigured) as caught:
        registry.resolve_adapter(provider, demo_mode=False)
    assert caught.value.detail_code == "no_adapter_for_provider"


def test_the_shipped_registry_holds_exactly_the_aws_factory() -> None:
    # M4.4 changes this on purpose; until then a stray registration is a mistake.
    assert set(registry._FACTORIES) == {"AWS"}


def test_aws_is_registered_but_unconfigured_until_the_document_version_is_set() -> None:
    with pytest.raises(NotConfigured) as caught:
        registry.resolve_adapter("AWS", demo_mode=False)
    assert caught.value.detail_code == "document_version_not_set"


def test_a_registered_provider_resolves_and_others_still_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(registry._FACTORIES, "AWS", ScriptedAdapter)
    assert isinstance(registry.resolve_adapter("AWS", demo_mode=False), ScriptedAdapter)
    with pytest.raises(ProviderError):
        registry.resolve_adapter("AZURE", demo_mode=False)


def test_demo_mode_wins_over_a_registered_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry._FACTORIES, "AWS", ScriptedAdapter)
    adapter: ProviderAdapter = registry.resolve_adapter("AWS", demo_mode=True)
    assert isinstance(adapter, DemoAdapter)


def test_the_default_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        demo_mode = True

    monkeypatch.setattr(registry, "get_settings", lambda: Fake())
    assert isinstance(registry.resolve_adapter("AWS"), DemoAdapter)
    Fake.demo_mode = False
    with pytest.raises(NotConfigured):
        registry.resolve_adapter("AWS")
