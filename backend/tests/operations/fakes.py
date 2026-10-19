"""A scriptable provider for the operation worker tests (test-only; never under ``app/``).

It can be told to fail in every way the worker has a rule for, and it counts what it was asked, so
a test can say "zero invocations" and mean it. It extends the conformance harness's
``ScriptedAdapter`` and changes only what a scenario needs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.providers.errors import ProviderError
from app.providers.types import (
    AdapterResult,
    ExecutionHandle,
    ExecutionStatus,
    ServerInfo,
    ServerRef,
    ServiceAction,
    ServiceRef,
    ServiceSnapshot,
)
from tests.providers.scripted import ScriptedAdapter


class Scripted(ScriptedAdapter):
    def __init__(
        self,
        *,
        unit: str = "web.service",
        before: str = "INACTIVE",
        after: tuple[str, ...] = ("ACTIVE",),
        statuses: tuple[str, ...] = ("IN_PROGRESS", "COMPLETED"),
        submit_error: BaseException | None = None,
        status_error: ProviderError | None = None,
        status_errors_first: int = 0,
        info_resource: str | None = None,
        list_error: ProviderError | None = None,
        read_state: str = "ok",
        provider_reason: str | None = None,
        on_status: Callable[[], None] | None = None,
        after_unit: str | None = None,
    ) -> None:
        super().__init__()
        self.unit = unit
        self.before = before
        self.after = after
        self.statuses = list(statuses)
        self.submit_error = submit_error
        self.status_error = status_error
        self.status_errors_first = status_errors_first
        self.info_resource = info_resource
        self.list_error = list_error
        self.read_state = read_state
        self.provider_reason = provider_reason
        self.on_status = on_status
        self.after_unit = after_unit
        self.submit_attempts = 0
        self.list_calls = 0
        self.info_calls = 0
        self.status_calls = 0

    def get_server_info(self, ref: ServerRef) -> AdapterResult[ServerInfo]:
        self.info_calls += 1
        return self._ok(
            ServerInfo(self.info_resource or ref.provider_resource_id, ref.region, "up")
        )

    def list_managed_services(self, ref: ServerRef) -> AdapterResult[list[ServiceSnapshot]]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        if self.list_calls > 1 and self.read_state == "stale":
            # Stale carries a value (the invariant), but a stale value cannot prove an end state.
            return AdapterResult(
                [ServiceSnapshot(self.unit, self.after[-1])],  # type: ignore[arg-type]
                "stale",
                self.adapter_id,
                self.demo,
                datetime.now(UTC),
            )
        if self.list_calls > 1 and self.read_state != "ok":
            return AdapterResult(None, self.read_state, self.adapter_id, self.demo)  # type: ignore[arg-type]
        if self.list_calls == 1:
            state = self.before
        else:
            index = min(self.list_calls - 2, len(self.after) - 1)
            state = self.after[index]
        unit = self.unit if self.list_calls == 1 or self.after_unit is None else self.after_unit
        return self._ok([ServiceSnapshot(unit, state)])  # type: ignore[arg-type]

    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        self.submit_attempts += 1
        if self.submit_error is not None:
            raise self.submit_error
        return super()._submit(ref, service, action, operation_id=operation_id)

    def get_operation_status(self, handle: ExecutionHandle) -> AdapterResult[ExecutionStatus]:
        self.status_calls += 1
        if self.on_status is not None:
            self.on_status()
        if self.status_error is not None:
            raise self.status_error
        if self.status_calls <= self.status_errors_first:
            raise _transient()
        state = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return AdapterResult(
            ExecutionStatus(state, self.provider_reason),  # type: ignore[arg-type]
            "ok",
            self.adapter_id,
            self.demo,
            datetime.now(UTC),
        )


def _transient() -> ProviderError:
    from app.providers.errors import Timeout

    return Timeout(adapter_id="scripted")
