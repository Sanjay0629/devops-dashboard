"""Value objects, the result envelope, capabilities and the gated ``execute_operation``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.providers.base import ProviderAdapter
from app.providers.capabilities import (
    CapabilitySet,
    Concurrency,
    Latency,
    Support,
    ensure_supported,
)
from app.providers.errors import NotConfigured, UnsupportedOperation
from app.providers.types import (
    AdapterResult,
    ExecutionHandle,
    ServerRef,
    ServiceAction,
    ServiceRef,
)

NOW = datetime(2026, 10, 19, tzinfo=UTC)
SERVER = ServerRef("i-0abc123", "000000000000", "AWS_SSM", "placeholder-region-1")
SERVICE = ServiceRef(uuid.uuid4(), "nginx.service")


def caps(**overrides: Support) -> CapabilitySet:
    actions = dict.fromkeys(ServiceAction, Support.SUPPORTED) | {
        ServiceAction(k): v for k, v in overrides.items()
    }
    return CapabilitySet(
        Support.SUPPORTED, Support.SUPPORTED, actions, True, Concurrency.NONE, Latency.IMMEDIATE
    )


# --- value objects ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unit",
    ["", "nginx", "nginx.exe", "a b.service", "a;rm -rf /.service", "a\n.service",
     "nginx.service\n", "../x.service", "x" * 129 + ".service"],
)  # fmt: skip
def test_a_bad_unit_name_is_refused_without_echoing_it(unit: str) -> None:
    with pytest.raises(ValueError) as caught:
        ServiceRef(uuid.uuid4(), unit)
    assert unit.strip() not in str(caught.value) or unit.strip() == ""


@pytest.mark.parametrize("unit", ["nginx.service", "php8.3-fpm.service", "a@b.socket", "t.timer"])
def test_a_registered_shape_is_accepted(unit: str) -> None:
    assert ServiceRef(uuid.uuid4(), unit).unit_name == unit


def test_service_id_must_be_a_uuid() -> None:
    with pytest.raises(ValueError, match="service_id"):
        ServiceRef("not-a-uuid", "nginx.service")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider_resource_id": "i-1\n"},
        {"provider_resource_id": ""},
        {"account_reference": " x"},
        {"management_method": "aws ssm"},
        {"region": "us east"},
    ],
)
def test_a_malformed_server_ref_is_refused(kwargs: dict[str, str]) -> None:
    base = {"provider_resource_id": "i-1", "account_reference": "1", "management_method": "NONE"}
    with pytest.raises(ValueError, match="malformed"):
        ServerRef(**(base | kwargs))


def test_an_azure_style_resource_id_is_accepted() -> None:
    assert ServerRef("/subscriptions/s/resourceGroups/g/vm/v", "sub-1", "AZURE_ARC")


@pytest.mark.parametrize("bad", ["", "a b", "x" * 257, "a\n"])
def test_a_bad_execution_id_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="external_execution_id"):
        ExecutionHandle("demo", bad, NOW)


def test_a_handle_needs_an_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ExecutionHandle("demo", "id-1", datetime(2026, 10, 19))  # noqa: DTZ001


# --- the result envelope ---------------------------------------------------------------------


def test_ok_needs_a_value_and_a_time() -> None:
    assert AdapterResult([], "ok", "demo", True, NOW).value == []  # empty is a value, not unknown
    with pytest.raises(ValueError, match="value"):
        AdapterResult(None, "ok", "demo", True, NOW)
    with pytest.raises(ValueError, match="collected_at"):
        AdapterResult("x", "ok", "demo", True, None)


@pytest.mark.parametrize("state", ["unavailable", "unsupported", "not_configured"])
def test_an_unknown_never_carries_a_substitute_value(state: Any) -> None:
    with pytest.raises(ValueError, match="value"):
        AdapterResult("x", state, "demo", True)
    with pytest.raises(ValueError, match="collected_at"):
        AdapterResult(None, state, "demo", True, NOW)
    assert AdapterResult(None, state, "demo", True, reason="why").value is None


def test_stale_keeps_its_value_and_time() -> None:
    assert AdapterResult("x", "stale", "demo", False, NOW).data_state == "stale"


def test_a_naive_collected_at_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        AdapterResult("x", "ok", "demo", False, datetime(2026, 1, 1))  # noqa: DTZ001


def test_a_reason_is_a_closed_code() -> None:
    with pytest.raises(ValueError, match="reason"):
        AdapterResult(None, "unavailable", "demo", False, reason="Access denied: arn:aws")


def test_the_flag_is_kept_exactly() -> None:
    assert AdapterResult(None, "not_configured", "demo", True).demo_mode is True
    assert AdapterResult(None, "not_configured", "demo", False).demo_mode is False


def test_a_non_utc_aware_time_is_fine() -> None:
    assert AdapterResult("x", "ok", "demo", False, datetime.now(timezone(timedelta(hours=5))))


# --- capabilities ----------------------------------------------------------------------------


def test_every_action_must_be_named() -> None:
    actions = dict.fromkeys(ServiceAction, Support.SUPPORTED)
    del actions[ServiceAction.STOP]
    with pytest.raises(ValueError, match="every ServiceAction"):
        CapabilitySet(
            Support.SUPPORTED, Support.SUPPORTED, actions, True, Concurrency.NONE, Latency.SECONDS
        )


def test_the_action_map_cannot_be_mutated_afterwards() -> None:
    source = dict.fromkeys(ServiceAction, Support.SUPPORTED)
    capabilities = caps()
    with pytest.raises(TypeError):
        capabilities.actions[ServiceAction.STOP] = Support.UNSUPPORTED  # type: ignore[index]
    built = CapabilitySet(
        Support.SUPPORTED, Support.SUPPORTED, source, True, Concurrency.NONE, Latency.SECONDS
    )
    source[ServiceAction.STOP] = Support.UNSUPPORTED
    assert built.actions[ServiceAction.STOP] is Support.SUPPORTED


def test_supported_passes_the_gate() -> None:
    ensure_supported(caps(), ServiceAction.RESTART, adapter_id="demo")


def test_unsupported_is_reported_as_unsupported() -> None:
    with pytest.raises(UnsupportedOperation) as caught:
        ensure_supported(
            caps(logs_tail=Support.UNSUPPORTED), ServiceAction.LOGS_TAIL, adapter_id="azure"
        )
    assert caught.value.detail_code == "action_logs_tail"
    assert caught.value.adapter_id == "azure"


def test_not_configured_is_a_different_answer_from_unsupported() -> None:
    with pytest.raises(NotConfigured):
        ensure_supported(caps(stop=Support.NOT_CONFIGURED), ServiceAction.STOP, adapter_id="demo")


# --- the base class --------------------------------------------------------------------------


class Recorder(ProviderAdapter):
    adapter_id = "recorder"

    def __init__(self, capabilities: CapabilitySet, stamp: str = "recorder") -> None:
        self._caps = capabilities
        self._stamp = stamp
        self.submitted: list[ServiceAction] = []

    def capabilities(self) -> CapabilitySet:
        return self._caps

    def get_server_info(self, ref: ServerRef) -> Any:
        raise NotImplementedError

    check_connectivity = list_managed_services = get_operation_status = get_server_info  # type: ignore[assignment]

    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        self.submitted.append(action)
        return ExecutionHandle(self._stamp, "exec-1", NOW)


def run(adapter: ProviderAdapter, action: ServiceAction = ServiceAction.RESTART) -> ExecutionHandle:
    return adapter.execute_operation(SERVER, SERVICE, action, operation_id=uuid.uuid4())


def test_a_supported_action_is_submitted_once() -> None:
    adapter = Recorder(caps())
    assert run(adapter).external_execution_id == "exec-1"
    assert adapter.submitted == [ServiceAction.RESTART]


@pytest.mark.parametrize("support", [Support.UNSUPPORTED, Support.NOT_CONFIGURED])
def test_a_gated_action_never_reaches_the_provider(support: Support) -> None:
    adapter = Recorder(caps(restart=support))
    with pytest.raises((UnsupportedOperation, NotConfigured)):
        run(adapter)
    assert adapter.submitted == []


def test_the_gate_is_per_action() -> None:
    adapter = Recorder(caps(stop=Support.UNSUPPORTED))
    run(adapter, ServiceAction.START)
    with pytest.raises(UnsupportedOperation):
        run(adapter, ServiceAction.STOP)
    assert adapter.submitted == [ServiceAction.START]


def test_a_handle_stamped_for_another_adapter_is_a_bug() -> None:
    with pytest.raises(RuntimeError, match="another adapter"):
        run(Recorder(caps(), stamp="other"))


def test_overriding_execute_operation_is_refused_at_class_creation() -> None:
    with pytest.raises(TypeError, match="execute_operation"):

        class Bypass(Recorder):
            def execute_operation(self, *a: Any, **k: Any) -> ExecutionHandle:  # pragma: no cover
                raise AssertionError


def test_an_adapter_without_submit_cannot_be_built() -> None:
    class Incomplete(ProviderAdapter):
        adapter_id = "incomplete"

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]
