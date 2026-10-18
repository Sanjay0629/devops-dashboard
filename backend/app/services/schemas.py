"""Request and response shapes for §16 (docs/API_CONTRACTS.md).

The response models are built field by field in ``service.py``, so a column
added to ``services`` later cannot appear in a response by accident.

**Four fields are always ``null`` in M2.4, and that is the contract, not a gap
in the code** (``OPEN-29``): ``sub_state``, ``load_state``, ``enabled`` and
``main_pid`` are not exposed by ``node_exporter``'s systemd collector or by
``systemd_exporter``. They are typed as optional values rather than removed so
that a restricted agent can fill them later without a breaking change, and so
that a client written against §16.1 as published keeps working. ``null`` means
unknown; it is never rendered as ``0``, ``false`` or an empty string.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.inventory.models import SERVICE_NAME_PATTERN
from app.inventory.schemas import CursorPage, DataState, DisplayName, Environment
from app.logs.schemas import LabelLevel
from app.metrics.schemas import UnavailableReason
from app.services.normalise import ServiceState

ServiceName = Annotated[str, Field(min_length=3, max_length=128)]
_SERVICE_NAME = re.compile(SERVICE_NAME_PATTERN)

ServiceAction = Literal["start", "stop", "restart", "status", "logs_tail"]
# AD-21's level values, one definition (M3.2): this was the journald spelling
# (`notice`, `warning`) while Loki holds `warn` and has no `notice`, so a filter on
# either could only ever match nothing. §16.7 never answered before M3.2, so no
# client depended on the old values.
LogLevel = LabelLevel


def _check_service_name(value: str) -> str:
    # Defence in depth only: *registration*, not this pattern, is the control
    # (docs/SECURITY.md §15). The value is not echoed back (§8).
    if _SERVICE_NAME.fullmatch(value) is None:
        raise ValueError(
            "must be a systemd unit name ending in .service, .socket, .timer or .target"
        )
    return value


class ServiceItem(BaseModel):
    """A §16.1/§16.2 row. ``state`` is the verdict; the rest is how to read it."""

    model_config = ConfigDict(extra="forbid")

    service_id: uuid.UUID
    server_id: uuid.UUID
    server_display_name: str
    environment: Environment
    service_name: str
    display_name: str | None
    application_id: uuid.UUID | None
    monitoring_enabled: bool
    state: ServiceState
    #: ``null`` in M2.4 — see the module docstring.
    sub_state: str | None
    load_state: str | None
    enabled: bool | None
    main_pid: int | None
    #: ``null`` on lists (served from the cache, which holds a state and a
    #: time); the detail endpoint reads it live.
    restart_count: int | None
    last_state_change_at: datetime | None
    data_state: DataState
    unavailable_reason: UnavailableReason | None
    collected_at: datetime | None
    server_reachable: bool | None
    #: A UI convenience that carries no authority (mandatory rule 4).
    allowed_actions: list[ServiceAction]
    approval_required: bool


class ServiceDetail(ServiceItem):
    """§16.4: the live reading, rather than the cache."""

    #: When the unit last became active. **Not** a last-state-change time
    #: (``OPEN-29``); ``null`` when the unit is not running.
    started_at: datetime | None


class ServiceList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ServiceItem]
    page: CursorPage
    demo_mode: bool


class ServiceRegisterRequest(BaseModel):
    """§16.3. ``action_policy_id`` omitted means **no actions are permitted**."""

    model_config = ConfigDict(extra="forbid")

    service_name: ServiceName
    display_name: DisplayName | None = None
    application_id: uuid.UUID | None = None
    monitoring_enabled: bool = True
    action_policy_id: uuid.UUID | None = None

    _service_name = field_validator("service_name")(_check_service_name)


class ServiceUpdateRequest(BaseModel):
    """§16.5. Only the fields present are changed; ``service_name`` is immutable."""

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    application_id: uuid.UUID | None = None
    monitoring_enabled: bool | None = None
    action_policy_id: uuid.UUID | None = None
