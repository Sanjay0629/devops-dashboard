"""Request and response models for the alert endpoints (API_CONTRACTS.md §20)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


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
