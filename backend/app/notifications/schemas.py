"""Request and response models for the notification endpoints.

API_CONTRACTS.md §20.6 and §26.8 - §26.10.

``extra="forbid"`` everywhere (CLAUDE.md §5). A channel is described by a *reference* to its
destination; no response carries a resolved value, and a request that tries to put a URL
where the reference goes is refused without being echoed (``SECURITY.md`` §9).
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.notifications.rules import RoutingRules

ChannelType = Literal["EMAIL", "TEAMS", "SLACK", "WEBHOOK"]
ChannelStatus = Literal["ACTIVE", "DISABLED"]
DeliveryStatus = Literal["PENDING", "SENT", "FAILED", "ABANDONED"]


def _plain_text(value: str) -> str:
    """A name is one line of printable text: no control or format characters."""
    value = value.strip()
    if not value or any(unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} for c in value):
        raise ValueError("must be one line of printable text")
    return value


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    channel_type: ChannelType
    #: ``env://NAME`` for SLACK / TEAMS / WEBHOOK; the address itself for EMAIL.
    destination_reference: str = Field(min_length=1, max_length=512)
    routing_rules: RoutingRules = Field(default_factory=RoutingRules)
    status: ChannelStatus = "ACTIVE"

    _name = field_validator("name")(_plain_text)


class ChannelUpdate(BaseModel):
    """Everything optional. The type cannot change: it would change what the reference means."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    destination_reference: str | None = Field(default=None, min_length=1, max_length=512)
    routing_rules: RoutingRules | None = None
    status: ChannelStatus | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return None if value is None else _plain_text(value)


class DeliveryHealth(BaseModel):
    """What this channel's recent deliveries say about it. Counts, never destinations."""

    model_config = ConfigDict(extra="forbid")

    pending: int
    sent_24h: int
    failed_24h: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    #: A closed reason code from the newest failed delivery.
    last_error: str | None


class ChannelOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    channel_type: ChannelType
    destination_reference: str
    #: The stored rules as written. ``routing_rules_valid`` is ``false`` for a row somebody
    #: edited into something the rule schema rejects -- such a channel delivers nothing.
    routing_rules: dict[str, object]
    routing_rules_valid: bool
    status: ChannelStatus
    created_at: datetime
    updated_at: datetime
    deliveries: DeliveryHealth


class ChannelList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ChannelOut]
    #: ``true`` when the deployment can send mail at all (``DCP_SMTP_HOST``): the UI uses it to
    #: say why an EMAIL channel cannot be registered, instead of letting the save fail.
    email_available: bool
    demo_mode: bool


class DeliveryOut(BaseModel):
    """One (alert, channel) delivery (§20.2). ``last_error`` is a closed reason code."""

    model_config = ConfigDict(extra="forbid")

    delivery_id: uuid.UUID
    channel_id: uuid.UUID
    channel_name: str
    channel_type: ChannelType
    status: DeliveryStatus
    attempt_count: int
    last_error: str | None
    next_attempt_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    #: ``true`` only for ``FAILED`` / ``ABANDONED`` on a channel that is ``ACTIVE``.
    retryable: bool
