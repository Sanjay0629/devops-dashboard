"""Metrics response models (docs/API_CONTRACTS.md §15.4 and §26.1).

Only the two shapes M2.1 serves live here. The metric *value* envelopes of
§15.1–§15.3 are M2.2's, and writing them now would mean publishing a contract
for endpoints that cannot answer — the same reason
``app/dashboard/schemas.py`` reports a section as ``not_configured`` instead
of guessing at its shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.inventory.schemas import OperatingSystem

#: §26.1's per-integration verdict. ``not_configured`` is a first-class
#: answer, not an error: an integration nobody has configured is not
#: "unavailable", and conflating the two is how a status page starts telling
#: an operator their estate is broken when it is merely unbuilt.
IntegrationState = Literal["ok", "degraded", "unavailable", "not_configured"]


class MetricTemplateInfo(BaseModel):
    """One catalogue entry (§15.4).

    ``applies_to_os`` is the honest list, not a parity assumption: a template
    absent for an OS family is ``unsupported`` on that family, and the client
    is told so here rather than discovering it as an empty chart (``OPEN-09``).
    """

    model_config = ConfigDict(extra="forbid")

    template: str
    unit: str
    description: str
    applies_to_os: list[OperatingSystem]
    #: ``null`` where no default is universally meaningful — see
    #: ``app/metrics/templates.py``. Absent thresholds are not zero
    #: thresholds.
    default_thresholds: dict[str, float] | None = None


class MetricTemplateList(BaseModel):
    """§15.4. A static catalogue, so no paging and no cursor."""

    model_config = ConfigDict(extra="forbid")

    templates: list[MetricTemplateInfo]
    #: Named so a client never has to infer where a number came from
    #: (PRD §4.4: the source is part of the answer).
    source: Literal["prometheus"] = "prometheus"
    demo_mode: bool


class IntegrationStatus(BaseModel):
    """§26.1's per-integration row."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    status: IntegrationState
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    #: Redacted and generic by construction: a reason code, never a provider
    #: message or a URL (docs/SECURITY.md §20, mandatory rule 5).
    last_error_message: str | None = None
    #: ``null`` for an integration that has no client, so no breaker.
    circuit_state: Literal["closed", "open", "half_open"] | None = None
    demo_mode: bool


class IntegrationStatusList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integrations: list[IntegrationStatus]
