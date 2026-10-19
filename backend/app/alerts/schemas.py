"""Request and response models for the alert endpoints (API_CONTRACTS.md §20)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.inventory.schemas import CursorPage
from app.notifications.schemas import DeliveryOut


class WebhookAlertResult(BaseModel):
    """What happened to one alert of a delivery. Never echoes alert content."""

    model_config = ConfigDict(extra="forbid")

    index: int
    accepted: bool
    #: ``created``, ``updated``, ``resolved``, ``ignored`` (a ``resolved`` for nothing
    #: open) or ``watchdog``, when accepted.
    action: Literal["created", "updated", "resolved", "ignored", "watchdog"] | None = None
    #: ``malformed``, ``unknown_rule``, ``overflow`` or ``database_rejected``, when not.
    reason: Literal["malformed", "unknown_rule", "overflow", "database_rejected"] | None = None


class WebhookResponse(BaseModel):
    """The ``202`` body (§20.5): per-alert accept/reject, so one bad alert rejects only itself."""

    model_config = ConfigDict(extra="forbid")

    accepted: int
    rejected: int
    #: Alerts Alertmanager dropped before sending (its ``max_alerts``); not recoverable.
    truncated_by_sender: int
    results: list[WebhookAlertResult]


# --- reads and lifecycle (§20.1 - §20.4) ----------------------------------------------------

AlertStatus = Literal["FIRING", "ACKNOWLEDGED", "RESOLVED", "EXPIRED"]
AlertSeverity = Literal["INFO", "WARNING", "CRITICAL"]
AlertSource = Literal[
    "PROMETHEUS", "DCP_HEALTH_CHECK", "DCP_OPERATION", "DCP_DEPLOYMENT", "DCP_SYSTEM", "MANUAL"
]


class UserRef(BaseModel):
    """Who, by name. No email: an operator needs to know whom, not how to reach them."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    display_name: str


class TargetRef(BaseModel):
    """The row an alert is about, **as found by lookup** (``OPEN-45``), never as labelled."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["server", "service", "application", "deployment", "operation"]
    id: uuid.UUID
    display_name: str | None


class AlertLinks(BaseModel):
    """API routes of what the alert is about (PRD §4.8 "links to affected ...")."""

    model_config = ConfigDict(extra="forbid")

    server: str | None = None
    service: str | None = None


class AlertSummary(BaseModel):
    """A list row (§20.1). Alert text is untrusted: render it as text."""

    model_config = ConfigDict(extra="forbid")

    alert_id: uuid.UUID
    source: AlertSource
    rule_name: str | None
    severity: AlertSeverity
    status: AlertStatus
    summary: str
    target: TargetRef | None
    environment: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    acknowledged_by: UserRef | None
    acknowledged_at: datetime | None
    assigned_to: UserRef | None
    #: Set only when the **source** said the problem cleared. Acknowledgement never
    #: sets it, and ``EXPIRED`` does not (PRD §4.8).
    resolved_at: datetime | None
    links: AlertLinks


class ConditionCoverage(BaseModel):
    """What this milestone produces alerts for, and what it does not yet (``OPEN-40``)."""

    model_config = ConfigDict(extra="forbid")

    produced: list[str]
    not_produced_yet: list[dict[str, str]]
    #: ACTIVE notification channels (``OPEN-56``). ``0`` means alerts are shown here and
    #: sent nowhere, which a page should say rather than leave to be assumed.
    notification_channels_active: int = 0


class AlertList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AlertSummary]
    page: CursorPage
    coverage: ConditionCoverage
    demo_mode: bool


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    event: Literal["first_seen", "acknowledged", "assigned", "unassigned", "resolved", "expired"]
    actor: str | None = None
    #: For ``assigned``: who it was assigned to.
    subject: str | None = None
    note: str | None = None


class PreviousOccurrence(BaseModel):
    """An earlier, closed alert for the same condition on the same target."""

    model_config = ConfigDict(extra="forbid")

    alert_id: uuid.UUID
    status: AlertStatus
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    occurrence_count: int


class AlertDetail(AlertSummary):
    """§20.2: the row, its own timeline and its recurrences."""

    #: Stored labels and annotations, as text. Untrusted.
    details: dict[str, object]
    history: list[HistoryEntry]
    previous_occurrences: list[PreviousOccurrence]
    #: Who was told, and how it went (``OPEN-56``). Reason codes are closed; never a URL.
    notifications: list[DeliveryOut] = Field(default_factory=list)


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class AssignRequest(BaseModel):
    """``assignee_id`` ``null`` unassigns."""

    model_config = ConfigDict(extra="forbid")

    assignee_id: uuid.UUID | None
    note: str | None = Field(default=None, max_length=500)
