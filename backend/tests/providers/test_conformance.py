"""The conformance harness, and proof that it can fail."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.providers.base import ProviderAdapter
from app.providers.demo import DemoAdapter
from app.providers.errors import NotConfigured
from app.providers.types import (
    AdapterResult,
    ConnectivityResult,
    ExecutionHandle,
    ServerInfo,
    ServerRef,
    ServiceAction,
    ServiceRef,
)
from tests.providers.conformance import check
from tests.providers.scripted import ScriptedAdapter

# M4.3 / M4.4 append their adapters here.
ADAPTERS: list[tuple[ProviderAdapter, bool]] = [(DemoAdapter(), True), (ScriptedAdapter(), False)]


@pytest.mark.parametrize(("adapter", "demo"), ADAPTERS, ids=lambda v: type(v).__name__)
def test_every_adapter_conforms(adapter: ProviderAdapter, demo: bool) -> None:
    check(adapter, demo=demo)


# --- adapters written to break one rule each -------------------------------------------------


class Leaky(ScriptedAdapter):
    def check_connectivity(self, ref: ServerRef) -> Any:
        raise RuntimeError("boom arn:aws:iam::1:role/x")


class Misstamped(ScriptedAdapter):
    def get_server_info(self, ref: ServerRef) -> Any:
        return AdapterResult(
            ServerInfo("i-1", None, "x"), "ok", "someone_else", False, datetime.now(UTC)
        )


class WrongFlag(ScriptedAdapter):
    def get_server_info(self, ref: ServerRef) -> Any:
        return AdapterResult(
            ServerInfo("i-1", None, "x"), "ok", self.adapter_id, True, datetime.now(UTC)
        )


class NotAResult(ScriptedAdapter):
    def check_connectivity(self, ref: ServerRef) -> Any:
        return {"reachable": True}


class FreeForm(ScriptedAdapter):
    def run(self, command: str) -> None: ...


class LiesAboutSupport(ScriptedAdapter):
    """Declares every action supported, then refuses one as not configured."""

    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        raise NotConfigured(adapter_id=self.adapter_id)


class StatusLeaks(ScriptedAdapter):
    def get_operation_status(self, ref: ServerRef, handle: ExecutionHandle) -> Any:
        raise KeyError(handle.external_execution_id)


class WrongHandleStamp(ScriptedAdapter):
    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        return ExecutionHandle("other", "x-1", datetime.now(UTC))


class ReachableConnectivityIsFine(ScriptedAdapter):
    """Not a violator: a sanity control that the harness does not reject a plain variation."""

    def check_connectivity(self, ref: ServerRef) -> AdapterResult[ConnectivityResult]:
        return self._ok(ConnectivityResult(False, "down"))


@pytest.mark.parametrize(
    "violator",
    [
        Leaky,
        Misstamped,
        WrongFlag,
        NotAResult,
        FreeForm,
        LiesAboutSupport,
        StatusLeaks,
        WrongHandleStamp,
    ],
)
def test_the_harness_rejects_an_adapter_that_breaks_a_rule(violator: type[ProviderAdapter]) -> None:
    with pytest.raises(AssertionError):
        check(violator(), demo=False)


def test_the_harness_accepts_a_plain_variation() -> None:
    check(ReachableConnectivityIsFine(), demo=False)
