"""Value objects and the result envelope (``OPEN-58``, ``OPEN-59``).

``ServerRef`` and ``ServiceRef`` are built from *stored rows* by the caller, never from a
request. Their constructors re-check the shape anyway: defence in depth, not the control (the
control is the registered ``services`` row and the closed :class:`ServiceAction`). A rejected
value is never echoed in the error -- it may be a hostname, an id or something worse.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

#: Same pattern as the ``services.service_name`` CHECK (docs/DATABASE.md §4.5).
UNIT_NAME: Final = re.compile(r"[A-Za-z0-9@._\-]{1,128}\.(service|socket|timer|target)")
_RESOURCE_ID: Final = re.compile(r"[A-Za-z0-9/][A-Za-z0-9._:/@=\-]{0,255}")
_ACCOUNT_REFERENCE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@=\-]{0,255}")
_REGION: Final = re.compile(r"[A-Za-z0-9._\-]{1,64}")
_METHOD: Final = re.compile(r"[A-Z][A-Z_]{0,31}")
_EXECUTION_ID: Final = re.compile(r"[A-Za-z0-9._:/\-]{1,256}")
_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ADAPTER_ID: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")


class ServiceAction(StrEnum):
    """The closed action set (docs/SECURITY.md §15). There is no other action."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    STATUS = "status"
    LOGS_TAIL = "logs_tail"


DataState = Literal["ok", "stale", "unavailable", "unsupported", "not_configured"]
ServiceState = Literal["ACTIVE", "INACTIVE", "FAILED", "ACTIVATING", "DEACTIVATING", "UNKNOWN"]
#: What the *provider* says about its own invocation. Deliberately not ``SUCCEEDED``: an
#: operation is ``SUCCEEDED`` only after a verification read (CLAUDE.md §6), and a name shared
#: with that state invites the shortcut.
ExecutionState = Literal["IN_PROGRESS", "COMPLETED", "FAILED", "CANCELLED"]


def _check(field: str, value: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field} is malformed")


@dataclass(frozen=True)
class ServerRef:
    """Which machine, as stored. Nothing here is a credential."""

    provider_resource_id: str
    account_reference: str
    management_method: str
    region: str | None = None

    def __post_init__(self) -> None:
        _check("provider_resource_id", self.provider_resource_id, _RESOURCE_ID)
        _check("account_reference", self.account_reference, _ACCOUNT_REFERENCE)
        _check("management_method", self.management_method, _METHOD)
        if self.region is not None:
            _check("region", self.region, _REGION)


@dataclass(frozen=True)
class ServiceRef:
    """A registered ``services`` row's identity. ``unit_name`` is the only name a provider sees."""

    service_id: uuid.UUID
    unit_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.service_id, uuid.UUID):
            raise ValueError("service_id is malformed")
        _check("unit_name", self.unit_name, UNIT_NAME)


@dataclass(frozen=True)
class ExecutionHandle:
    """The provider's id for an invocation. M4.2 persists it **before** polling (AD-08)."""

    adapter_id: str
    external_execution_id: str
    submitted_at: datetime

    def __post_init__(self) -> None:
        _check("adapter_id", self.adapter_id, _ADAPTER_ID)
        _check("external_execution_id", self.external_execution_id, _EXECUTION_ID)
        if self.submitted_at.tzinfo is None:
            raise ValueError("submitted_at must be timezone-aware")


@dataclass(frozen=True)
class ServerInfo:
    provider_resource_id: str
    region: str | None
    provider_status: str


@dataclass(frozen=True)
class ConnectivityResult:
    reachable: bool
    provider_status: str


@dataclass(frozen=True)
class ServiceSnapshot:
    unit_name: str
    state: ServiceState


@dataclass(frozen=True)
class ExecutionStatus:
    state: ExecutionState
    reason_code: str | None = None


@dataclass(frozen=True)
class AdapterResult[T]:
    """What a read returns. ``value`` is ``None`` unless ``data_state`` is ``ok`` or ``stale``.

    ``demo_mode`` is set by the adapter that produced the result (``OPEN-59``), so the flag
    survives a queue or a log line; it is never read from settings at the API edge.
    """

    value: T | None
    data_state: DataState
    adapter_id: str
    demo_mode: bool
    collected_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _check("adapter_id", self.adapter_id, _ADAPTER_ID)
        has_value = self.data_state in ("ok", "stale")
        if has_value != (self.value is not None):
            raise ValueError("value is present exactly when data_state is ok or stale")
        if has_value != (self.collected_at is not None):
            raise ValueError("collected_at is present exactly when data_state is ok or stale")
        if self.collected_at is not None and self.collected_at.tzinfo is None:
            raise ValueError("collected_at must be timezone-aware")
        if self.reason is not None:
            _check("reason", self.reason, _CODE)
