"""A test-only adapter that really does everything, with canned answers.

It lives under ``tests/`` on purpose: in ``app/`` it could be mistaken for an integration
(CLAUDE.md §7). It exists to give the conformance harness a *supported* path to exercise, and
M4.2 will drive the operation state machine with it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.providers.base import ProviderAdapter
from app.providers.capabilities import CapabilitySet, Concurrency, Latency, Support
from app.providers.types import (
    AdapterResult,
    ConnectivityResult,
    ExecutionHandle,
    ExecutionStatus,
    ServerInfo,
    ServerRef,
    ServiceAction,
    ServiceRef,
    ServiceSnapshot,
)


class ScriptedAdapter(ProviderAdapter):
    adapter_id = "scripted"
    demo = False

    def __init__(self) -> None:
        self.submitted: list[tuple[ServiceAction, uuid.UUID]] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            Support.SUPPORTED,
            Support.SUPPORTED,
            dict.fromkeys(ServiceAction, Support.SUPPORTED),
            True,
            Concurrency.ADAPTER_DEFINED,
            Latency.SECONDS,
        )

    def _ok[T](self, value: T) -> AdapterResult[T]:
        return AdapterResult(value, "ok", self.adapter_id, self.demo, datetime.now(UTC))

    def get_server_info(self, ref: ServerRef) -> AdapterResult[ServerInfo]:
        return self._ok(ServerInfo(ref.provider_resource_id, ref.region, "scripted"))

    def check_connectivity(self, ref: ServerRef) -> AdapterResult[ConnectivityResult]:
        return self._ok(ConnectivityResult(True, "scripted"))

    def list_managed_services(
        self, ref: ServerRef, service: ServiceRef
    ) -> AdapterResult[list[ServiceSnapshot]]:
        return self._ok([ServiceSnapshot("nginx.service", "ACTIVE")])

    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        self.submitted.append((action, operation_id))
        return ExecutionHandle(
            self.adapter_id, f"scripted-{len(self.submitted)}", datetime.now(UTC)
        )

    def get_operation_status(
        self, ref: ServerRef, handle: ExecutionHandle
    ) -> AdapterResult[ExecutionStatus]:
        return self._ok(ExecutionStatus("COMPLETED"))
