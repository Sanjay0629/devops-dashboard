"""Response models for endpoints 24.1 and 24.2 (docs/API_CONTRACTS.md §24).

Read-only by construction: there is no request body anywhere under ``/audit-events`` that writes an
event. ``sanitized_metadata`` is already redacted when stored and is redacted **again** on the way
out (``OPEN-111``), so a row written before a redaction rule improved is read under today's rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.inventory.schemas import CursorPage


class AuditEventView(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    #: Preserved verbatim at write time, so the trail stays readable if the user is later renamed.
    actor_label: str
    action_type: str
    target_type: str | None
    target_id: uuid.UUID | None
    environment: str | None
    correlation_id: uuid.UUID
    approval_id: uuid.UUID | None
    outcome: str
    source_ip: str | None
    user_agent: str | None
    #: Untrusted text as far as a reader is concerned: render it as text, never as markup.
    sanitized_metadata: dict[str, Any]


class AuditEventList(BaseModel):
    items: list[AuditEventView]
    page: CursorPage
