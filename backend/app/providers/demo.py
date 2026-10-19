"""The demonstration adapter (AD-15, ``OPEN-63``).

**Rule: it never asserts a fact about a host.** It says what is true of itself -- its
capabilities, and the identity the caller already holds -- and raises ``NotConfigured`` for
anything that would state the condition of a machine or change it. "Always ok" connectivity
would present a mock as operational (CLAUDE.md §7), and an empty service list is the "no logs
found" lie in a new place. Synthetic fleet data stays where it already is: the
``[DEMO]``-labelled rows from ``app.inventory.seed``.

Every capability is ``not_configured`` rather than ``unsupported``: a real provider could do
these things; this deployment has not set one up.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import NoReturn

from app.providers.base import ProviderAdapter
from app.providers.capabilities import CapabilitySet, Concurrency, Latency, Support
from app.providers.errors import NotConfigured
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

_CAPABILITIES = CapabilitySet(
    inventory_sync=Support.NOT_CONFIGURED,
    connectivity_check=Support.NOT_CONFIGURED,
    actions=dict.fromkeys(ServiceAction, Support.NOT_CONFIGURED),
    async_handle=False,
    concurrency=Concurrency.NONE,
    latency=Latency.IMMEDIATE,
)


class DemoAdapter(ProviderAdapter):
    adapter_id = "demo"

    def capabilities(self) -> CapabilitySet:
        return _CAPABILITIES

    def get_server_info(self, ref: ServerRef) -> AdapterResult[ServerInfo]:
        # Echoes what the caller already holds. It looks nothing up, so it can invent nothing.
        return AdapterResult(
            ServerInfo(ref.provider_resource_id, ref.region, "demonstration"),
            "ok",
            self.adapter_id,
            True,
            collected_at=datetime.now(UTC),
        )

    def check_connectivity(self, ref: ServerRef) -> AdapterResult[ConnectivityResult]:
        self._refuse()

    def list_managed_services(self, ref: ServerRef) -> AdapterResult[list[ServiceSnapshot]]:
        self._refuse()

    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        # Unreachable through execute_operation (every action is gated); refused here as well
        # so that a future change to the gate cannot turn the demo adapter into a writer.
        self._refuse()

    def get_operation_status(self, handle: ExecutionHandle) -> AdapterResult[ExecutionStatus]:
        self._refuse()

    def _refuse(self) -> NoReturn:
        raise NotConfigured(adapter_id=self.adapter_id, detail_code="demonstration_mode")
