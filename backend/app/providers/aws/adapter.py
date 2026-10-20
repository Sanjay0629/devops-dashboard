"""The AWS Systems Manager adapter (``OPEN-79`` – ``OPEN-83``).

There is no native SSM read of a unit's state, so **every read is a Run Command** of the
registered ``DCP-ServiceControl`` document with ``Action=status`` (finding 3 of the M4.3 plan);
the document is the read/write boundary. The adapter sends the document to one instance id, with
typed parameters taken from stored rows, and parses the answer to a closed state set. Command
output is never stored or returned.

A write (``SendCommand`` for start / stop / restart) is sent once. ``Throttled`` is retried here,
because AWS refused it before acting; every other failure is left to the caller to reconcile.
"""

from __future__ import annotations

import random
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from app.providers.aws.api import (
    READ_SESSION_NAME,
    AwsApi,
    ClientFactory,
    Target,
    boto_client_factory,
    target_from,
)
from app.providers.aws.errors import ADAPTER_ID
from app.providers.base import ProviderAdapter
from app.providers.capabilities import CapabilitySet, Concurrency, Latency, Support
from app.providers.errors import (
    AuthorizationError,
    NotFound,
    ProviderUnavailable,
    Throttled,
    Timeout,
)
from app.providers.types import (
    AdapterResult,
    ConnectivityResult,
    ExecutionHandle,
    ExecutionState,
    ExecutionStatus,
    ServerInfo,
    ServerRef,
    ServiceAction,
    ServiceRef,
    ServiceSnapshot,
    ServiceState,
)

DOCUMENT_NAME: Final = "DCP-ServiceControl"
#: ``SendCommand`` on a write: at most this many attempts, all inside ``INVOKE_BOUND_SECONDS``.
THROTTLE_ATTEMPTS: Final = 3
INVOKE_BOUND_SECONDS: Final = 30.0
THROTTLE_BACKOFF_CAP_SECONDS: Final = 8.0
#: One read command (send + poll). A working default, not a measurement.
READ_BOUND_SECONDS: Final = 30.0
_READ_POLL_DELAYS: Final = (1.0, 1.0, 2.0, 2.0, 3.0)
#: GetCommandInvocation can say "does not exist" for a moment after SendCommand.
_EVENTUAL_CONSISTENCY_SECONDS: Final = 60.0
_UNIT_NOT_FOUND_EXIT: Final = 4
_COMMAND_ID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_CAPABILITIES: Final = CapabilitySet(
    inventory_sync=Support.SUPPORTED,
    connectivity_check=Support.SUPPORTED,
    actions={
        ServiceAction.START: Support.SUPPORTED,
        ServiceAction.STOP: Support.SUPPORTED,
        ServiceAction.RESTART: Support.SUPPORTED,
        ServiceAction.STATUS: Support.SUPPORTED,
        # The registered document has no such action; logs come from Loki. "We have not set it
        # up", not "AWS cannot" (OPEN-83).
        ServiceAction.LOGS_TAIL: Support.NOT_CONFIGURED,
    },
    async_handle=True,
    concurrency=Concurrency.ACCOUNT_THROTTLED,
    latency=Latency.SECONDS,
)

_PING_STATUS: Final = {
    "Online": "online",
    "ConnectionLost": "connection_lost",
    "Inactive": "inactive",
}
_UNIT_STATE: Final[dict[str, ServiceState]] = {
    "active": "ACTIVE",
    "inactive": "INACTIVE",
    "failed": "FAILED",
    "activating": "ACTIVATING",
    "deactivating": "DEACTIVATING",
}
_OUTPUT_TOKEN: Final = re.compile(r"[a-z][a-z-]{0,23}")
_IN_PROGRESS: Final = frozenset({"Pending", "InProgress", "Delayed", "Cancelling"})
_DETAIL_REASON: Final = {
    "DeliveryTimedOut": "delivery_timed_out",
    "ExecutionTimedOut": "execution_timed_out",
    "Undeliverable": "undeliverable",
    "AccessDenied": "access_denied",
    "InvalidPlatform": "invalid_platform",
    "Terminated": "terminated",
}


class AwsSsmAdapter(ProviderAdapter):
    adapter_id = ADAPTER_ID

    def __init__(
        self,
        *,
        document_version: str,
        client_factory: ClientFactory = boto_client_factory,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._version = document_version
        self.api = AwsApi(client_factory, clock=clock)
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter
        self._clock = clock

    # -- helpers ----------------------------------------------------------------------------

    def _target(self, ref: ServerRef) -> Target:
        return target_from(
            role_arn=ref.credential_reference,
            region=ref.region,
            account_reference=ref.account_reference,
        )

    def _ok[T](self, value: T) -> AdapterResult[T]:
        return AdapterResult(value, "ok", self.adapter_id, False, collected_at=self._clock())

    def _instance(self, target: Target, ref: ServerRef) -> dict[str, Any] | None:
        response = self.api.call(
            target,
            "ssm",
            "describe_instance_information",
            call="describe_instance_information",
            Filters=[{"Key": "InstanceIds", "Values": [ref.provider_resource_id]}],
        )
        found = response.get("InstanceInformationList") or []
        return found[0] if found else None

    # -- reads ------------------------------------------------------------------------------

    def capabilities(self) -> CapabilitySet:
        return _CAPABILITIES

    def get_server_info(self, ref: ServerRef) -> AdapterResult[ServerInfo]:
        info = self._instance(self._target(ref), ref)
        if info is None:
            raise NotFound(adapter_id=self.adapter_id, detail_code="instance_not_managed")
        return self._ok(
            ServerInfo(
                str(info["InstanceId"]),
                ref.region,
                _PING_STATUS.get(str(info.get("PingStatus")), "unknown"),
            )
        )

    def check_connectivity(self, ref: ServerRef) -> AdapterResult[ConnectivityResult]:
        info = self._instance(self._target(ref), ref)
        if info is None:
            # An answer, not an error: the host is simply not registered with SSM.
            return self._ok(ConnectivityResult(False, "not_registered_with_ssm"))
        status = _PING_STATUS.get(str(info.get("PingStatus")), "unknown")
        return self._ok(ConnectivityResult(status == "online", status))

    def list_managed_services(
        self, ref: ServerRef, service: ServiceRef
    ) -> AdapterResult[list[ServiceSnapshot]]:
        """The state of one registered unit, read by running the document with ``status``."""
        target = self._target(ref)
        response = self.api.call(
            target,
            "ssm",
            "send_command",
            call="send_command",
            **self._command(ref, ServiceAction.STATUS, service, comment="dcp-read"),
        )
        command_id = self._command_id(response)
        deadline = self._monotonic() + READ_BOUND_SECONDS
        attempt = 0
        while True:
            try:
                invocation = self._invocation(target, ref, command_id)
            except NotFound as error:
                if error.detail_code != "command_not_found":
                    raise
                invocation = {"Status": "Pending"}
            status = str(invocation.get("Status"))
            if status == "Success":
                return self._ok(
                    self._parse_state(service, str(invocation.get("StandardOutputContent", "")))
                )
            if status not in _IN_PROGRESS:
                return self._failed_read(invocation)
            if self._monotonic() >= deadline:
                raise Timeout(adapter_id=self.adapter_id, detail_code="status_command_timed_out")
            self._sleep(_READ_POLL_DELAYS[min(attempt, len(_READ_POLL_DELAYS) - 1)])
            attempt += 1

    def _parse_state(self, service: ServiceRef, output: str) -> list[ServiceSnapshot]:
        token = output.strip()
        if _OUTPUT_TOKEN.fullmatch(token) is None:
            raise ProviderUnavailable(adapter_id=self.adapter_id, detail_code="unparseable_output")
        # A systemd state this adapter does not model (reloading, maintenance, ...) is UNKNOWN:
        # honest, and it cannot verify a start or a stop.
        return [ServiceSnapshot(service.unit_name, _UNIT_STATE.get(token, "UNKNOWN"))]

    def _failed_read(self, invocation: dict[str, Any]) -> AdapterResult[list[ServiceSnapshot]]:
        status = str(invocation.get("Status"))
        details = str(invocation.get("StatusDetails", ""))
        if status == "Failed" and invocation.get("ResponseCode") == _UNIT_NOT_FOUND_EXIT:
            # The host answered: it has no such unit. Absent from the list, not an error.
            return self._ok([])
        if status == "TimedOut" or details in ("DeliveryTimedOut", "ExecutionTimedOut"):
            raise Timeout(adapter_id=self.adapter_id, detail_code="status_command_timed_out")
        if details in ("Undeliverable", "InvalidPlatform"):
            raise NotFound(adapter_id=self.adapter_id, detail_code="instance_not_managed")
        if details == "AccessDenied":
            raise AuthorizationError(adapter_id=self.adapter_id, detail_code="send_command_denied")
        raise ProviderUnavailable(adapter_id=self.adapter_id, detail_code="status_command_failed")

    # -- the write --------------------------------------------------------------------------

    def _command(
        self, ref: ServerRef, action: ServiceAction, service: ServiceRef, *, comment: str
    ) -> dict[str, Any]:
        """The SendCommand arguments. Every value is a closed type or a stored row's id."""
        return {
            "InstanceIds": [ref.provider_resource_id],
            "DocumentName": DOCUMENT_NAME,
            "DocumentVersion": self._version,
            "Parameters": {"Action": [action.value], "UnitName": [service.unit_name]},
            "Comment": comment,
            "TimeoutSeconds": 30,
            "MaxConcurrency": "1",
            "MaxErrors": "0",
        }

    def _command_id(self, response: dict[str, Any]) -> str:
        command_id = str(response.get("Command", {}).get("CommandId", ""))
        if _COMMAND_ID.fullmatch(command_id) is None:
            raise ProviderUnavailable(adapter_id=self.adapter_id, detail_code="unexpected_response")
        return command_id

    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        target = self._target(ref)
        arguments = self._command(ref, action, service, comment=f"dcp-op-{operation_id}")
        started = self._monotonic()
        for attempt in range(THROTTLE_ATTEMPTS):
            try:
                response = self.api.call(
                    target,
                    "ssm",
                    "send_command",
                    call="send_command",
                    session_name=f"dcp-{operation_id}",
                    write=True,
                    **arguments,
                )
                break
            except Throttled as throttled:
                # Refused before acting, so repeating it cannot run the action twice.
                if attempt == THROTTLE_ATTEMPTS - 1:
                    raise
                wait = (
                    throttled.retry_after
                    if throttled.retry_after is not None
                    else self._jitter() * min(THROTTLE_BACKOFF_CAP_SECONDS, 2.0**attempt)
                )
                if self._monotonic() - started + wait > INVOKE_BOUND_SECONDS:
                    raise
                self._sleep(wait)
        return ExecutionHandle(self.adapter_id, self._command_id(response), self._clock())

    # -- polling ----------------------------------------------------------------------------

    def _invocation(self, target: Target, ref: ServerRef, command_id: str) -> dict[str, Any]:
        return self.api.call(
            target,
            "ssm",
            "get_command_invocation",
            call="get_command_invocation",
            CommandId=command_id,
            InstanceId=ref.provider_resource_id,
        )

    def get_operation_status(
        self, ref: ServerRef, handle: ExecutionHandle
    ) -> AdapterResult[ExecutionStatus]:
        target = self._target(ref)
        try:
            invocation = self._invocation(target, ref, handle.external_execution_id)
        except NotFound as error:
            age = (self._clock() - handle.submitted_at).total_seconds()
            if error.detail_code == "command_not_found" and age < _EVENTUAL_CONSISTENCY_SECONDS:
                return self._ok(ExecutionStatus("IN_PROGRESS"))
            raise
        status = str(invocation.get("Status"))
        state: ExecutionState
        reason: str | None = None
        if status in _IN_PROGRESS:
            state = "IN_PROGRESS"
        elif status == "Success":
            state = "COMPLETED"
        elif status == "Cancelled":
            state, reason = "CANCELLED", "cancelled"
        elif status in ("Failed", "TimedOut"):
            state = "FAILED"
            reason = _DETAIL_REASON.get(
                str(invocation.get("StatusDetails", "")),
                "command_timed_out" if status == "TimedOut" else "command_failed",
            )
        else:
            raise ProviderUnavailable(adapter_id=self.adapter_id, detail_code="unmapped_status")
        return self._ok(ExecutionStatus(state, reason))


__all__ = ["READ_SESSION_NAME", "AwsSsmAdapter"]
