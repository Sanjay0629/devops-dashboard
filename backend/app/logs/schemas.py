"""The §19.1 request: structured filters and nothing else (AD-05).

``extra="forbid"`` is the first half of "no raw LogQL": a body carrying a
``query``, ``logql``, ``selector`` or ``regex`` field is a ``422`` before any
code runs. The second half is that nothing here is ever concatenated into a
query -- every field is a closed enum, a UUID, a bounded number or a string
validated against a fixed shape, and ``app/logs/logql.py`` re-checks each one
when it renders.

The closed value sets are AD-21's: they are what Alloy lets into Loki, so a
filter value outside them can only ever match nothing, and refusing it is more
honest than returning an empty page for it.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Final, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.inventory.schemas import Environment

SourceType = Literal["journald", "syslog", "nginx", "php_fpm", "docker", "application"]
#: Sources that need ``logs:read:system`` (SECURITY.md §4). ``application`` needs
#: only ``logs:read:application``. Everything else is system-only, so a source
#: type added later is restricted until someone decides otherwise.
SYSTEM_SOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {"journald", "syslog", "nginx", "php_fpm", "docker"}
)
#: AD-21's ``level`` values. NB: not the journald-flavoured ``LogLevel`` that the
#: M2.4 §16.7 query parameter uses (``notice``, ``warning``); see PROGRESS.
LabelLevel = Literal["debug", "info", "warn", "error", "critical", "unknown"]
PresetId = Literal[
    "http_5xx",
    "oom_killed",
    "connection_refused",
    "timeout",
    "permission_denied",
    "auth_failure",
    "segfault",
    "disk_full",
]
Direction = Literal["backward", "forward"]

#: AD-21: a plain name, or the fixed ``other``. The same shape Alloy enforces.
SERVICE_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")


def _service_shape(value: str) -> str:
    """``fullmatch``, not pydantic's ``pattern=``: that is a *search*, so
    ``x"} or {a="b`` passes it on the strength of its first character. The unit
    test that caught this is ``test_closed_values_and_shapes_are_enforced``."""
    if SERVICE_NAME.fullmatch(value) is None:
        raise ValueError("must be a plain unit or service name")
    return value


ServiceName = Annotated[str, Field(max_length=63), AfterValidator(_service_shape)]

MAX_SERVER_IDS: Final = 50
MAX_CONTAINS: Final = 256
DEFAULT_LIMIT: Final = 200
MAX_LIMIT: Final = 1_000


class LogQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: Annotated[AwareDatetime, Field(alias="from")]
    to: AwareDatetime
    server_ids: Annotated[
        list[uuid.UUID] | None, Field(min_length=1, max_length=MAX_SERVER_IDS)
    ] = None
    application_id: uuid.UUID | None = None
    environment: Environment | None = None
    service: ServiceName | None = None
    source_type: SourceType | None = None
    level: Annotated[list[LabelLevel] | None, Field(min_length=1, max_length=6)] = None
    contains: Annotated[str | None, Field(min_length=1, max_length=MAX_CONTAINS)] = None
    pattern_preset: PresetId | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT
    direction: Direction = "backward"
    cursor: Annotated[str | None, Field(min_length=1, max_length=1_024)] = None

    @field_validator("contains")
    @classmethod
    def _contains_is_printable(cls, value: str | None) -> str | None:
        """No control characters: a newline cannot appear inside one log entry's
        search text, and a NUL or escape has no business in a LogQL string."""
        if value is not None and any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            raise ValueError("must not contain control characters")
        return value

    @model_validator(mode="after")
    def _bounded_target(self) -> LogQueryRequest:
        """An unbounded query across the estate is refused (§19.1)."""
        if self.server_ids is None and self.application_id is None and self.environment is None:
            raise ValueError(
                "at least one of server_ids, application_id or environment is required"
            )
        return self
