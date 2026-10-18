"""The Alertmanager webhook payload, validated as untrusted input (SECURITY.md §21).

The shape is **what a real Alertmanager 0.27 sent** in the M3.3 spike, not what the
documentation says: version ``"4"``; an envelope of ``receiver``, ``status``,
``groupLabels``, ``commonLabels``, ``commonAnnotations``, ``externalURL``, ``groupKey``,
``alerts`` and -- **only when Alertmanager's own ``max_alerts`` cut the list short** --
``truncatedAlerts``; and per alert ``status``, ``labels``, ``annotations``, ``startsAt``,
``endsAt`` (the zero time while firing), ``generatorURL`` and ``fingerprint``.

Two layers, deliberately. The **envelope** is strict (``extra="forbid"``, a closed
version): a payload that is not Alertmanager's is refused whole. The **alerts** are held
as plain dictionaries here and validated one at a time by :class:`WebhookAlert`, so one
malformed alert cannot reject the batch (API_CONTRACTS.md §20.5).

Nothing here is trusted for meaning. Label and annotation text is length-capped and
shaped, and later stored and rendered as text; ``generatorURL`` is accepted and then
**dropped** -- it is a URL from outside, and DCP never fetches or stores one
(mandatory rule 9).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.inventory.models import SERVICE_NAME_PATTERN

#: Body ceiling, enforced while reading, not after.
MAX_BODY_BYTES: Final = 256 * 1024
#: Alerts processed per request. Alertmanager's own ``max_alerts`` is set to the same
#: number, so overflow here means something other than Alertmanager is calling.
MAX_ALERTS_PER_REQUEST: Final = 100
MAX_LABELS: Final = 40
MAX_ANNOTATIONS: Final = 20
MAX_NAME_LENGTH: Final = 64
MAX_LABEL_VALUE_LENGTH: Final = 200
MAX_ANNOTATION_VALUE_LENGTH: Final = 1_000

_LABEL_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_UUID_TEXT: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UNIT_NAME: Final = re.compile(SERVICE_NAME_PATTERN)


def _bounded_map(
    value: dict[str, str], *, limit: int, value_limit: int, what: str
) -> dict[str, str]:
    """Names are identifiers and values are bounded text; nothing else gets in.

    ``re.fullmatch``, not pydantic's ``pattern=``, which *searches* (the M3.2 trap).
    """
    if len(value) > limit:
        raise ValueError(f"at most {limit} {what}")
    for name, text in value.items():
        if len(name) > MAX_NAME_LENGTH or _LABEL_NAME.fullmatch(name) is None:
            raise ValueError(f"a {what[:-1]} name is not a valid identifier")
        if len(text) > value_limit:
            raise ValueError(f"a {what[:-1]} value is longer than {value_limit} characters")
    return value


class WebhookAlert(BaseModel):
    """One alert, strictly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime  # noqa: N815 - Alertmanager's spelling
    endsAt: datetime  # noqa: N815
    #: Accepted so the real payload validates, then never used: see the module docstring.
    generatorURL: str = Field(default="", max_length=2_048)  # noqa: N815
    fingerprint: str = Field(max_length=64)

    @field_validator("labels")
    @classmethod
    def _labels(cls, value: dict[str, str]) -> dict[str, str]:
        return _bounded_map(
            value, limit=MAX_LABELS, value_limit=MAX_LABEL_VALUE_LENGTH, what="labels"
        )

    @field_validator("annotations")
    @classmethod
    def _annotations(cls, value: dict[str, str]) -> dict[str, str]:
        return _bounded_map(
            value,
            limit=MAX_ANNOTATIONS,
            value_limit=MAX_ANNOTATION_VALUE_LENGTH,
            what="annotations",
        )


class WebhookEnvelope(BaseModel):
    """The request body. The alerts are not validated here (see the module docstring)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["4"]
    status: Literal["firing", "resolved"]
    receiver: str = Field(max_length=256)
    groupKey: str = Field(max_length=2_048)  # noqa: N815
    groupLabels: dict[str, str]  # noqa: N815
    commonLabels: dict[str, str]  # noqa: N815
    commonAnnotations: dict[str, str]  # noqa: N815
    externalURL: str = Field(max_length=2_048)  # noqa: N815
    #: Present only when Alertmanager dropped alerts to honour ``max_alerts``.
    truncatedAlerts: int = Field(default=0, ge=0, le=1_000_000)  # noqa: N815
    alerts: list[dict[str, Any]]

    @field_validator("groupLabels", "commonLabels")
    @classmethod
    def _label_maps(cls, value: dict[str, str]) -> dict[str, str]:
        return _bounded_map(
            value, limit=MAX_LABELS, value_limit=MAX_LABEL_VALUE_LENGTH, what="labels"
        )

    @field_validator("commonAnnotations")
    @classmethod
    def _annotation_map(cls, value: dict[str, str]) -> dict[str, str]:
        return _bounded_map(
            value,
            limit=MAX_ANNOTATIONS,
            value_limit=MAX_ANNOTATION_VALUE_LENGTH,
            what="annotations",
        )


class TargetLabels(BaseModel):
    """What the labels *claim* about the target. A claim, never a target."""

    model_config = ConfigDict(frozen=True)

    #: Parsed only if it is a canonical UUID; the lookup, not this, decides it is real.
    server_id: uuid.UUID | None
    #: Only the systemd unit shape ``services.service_name`` is constrained to.
    unit: str | None
    #: The raw ``instance`` label, capped: all that identifies an *unresolved* alert.
    instance: str | None


def parse_target_labels(labels: dict[str, str]) -> TargetLabels:
    """Pull the identifying claims out of an alert's labels, refusing malformed ones.

    A malformed ``server_id`` is ``None`` rather than an error: the alert is then
    *unlinked*, which is the specified outcome for labels that match nothing
    (SECURITY.md §21), not a reason to drop the alert.
    """
    raw_server = labels.get("server_id", "")
    server_id = uuid.UUID(raw_server) if _UUID_TEXT.fullmatch(raw_server) else None
    raw_unit = labels.get("name", "")
    unit = raw_unit if _UNIT_NAME.fullmatch(raw_unit) else None
    instance = labels.get("instance") or None
    return TargetLabels(server_id=server_id, unit=unit, instance=instance)
