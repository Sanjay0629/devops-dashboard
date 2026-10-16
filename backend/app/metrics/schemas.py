"""Metrics response models (docs/API_CONTRACTS.md §15.4 and §26.1).

M2.1 shipped the catalogue and status shapes; M2.2 adds the value envelopes of
§15.1–§15.3.

**A reading is a value or a reason, never a substitute.** ``value`` and
``points`` are ``null`` whenever ``data_state`` is not ``ok`` — an
``unavailable`` host has no CPU figure, and ``0`` would read as "idle"
(PRD §4.4). Inside ``points`` a missing sample is a ``null`` value at its own
timestamp, which is how a chart knows to draw a gap rather than a line.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.inventory.schemas import DataState, Environment, OperatingSystem
from app.metrics.thresholds import ThresholdLevel

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


# --- §15.1–§15.3: values ------------------------------------------------------

#: Why a reading has no value. A closed set, because the frontend renders one
#: message per reason and an unexpected string would render as itself. The
#: first four are the Prometheus client's own; the rest are decided before or
#: after a query. ``no_data`` is deliberately blunt: until M2.3 verifies
#: ``metrics_instance`` at registration, "nothing was scraped" and "the label
#: matches no target" are indistinguishable (PROGRESS, known limitation).
UnavailableReason = Literal[
    "provider_timeout",
    "provider_unreachable",
    "provider_error",
    "circuit_open",
    "no_data",
    "monitoring_disabled",
    "no_metrics_instance",
    "prometheus_not_configured",
    "unsupported_os",
]

#: ``[timestamp, value]``; ``value`` is ``null`` for a gap.
Point = tuple[datetime, float | None]


class MetricReading(BaseModel):
    """One current value (§15.1)."""

    model_config = ConfigDict(extra="forbid")

    template: str
    unit: str
    value: float | None
    data_state: DataState
    #: When the host was last scraped. ``null`` only where nothing was.
    collected_at: datetime | None = None
    stale_after_seconds: int | None = None
    unavailable_reason: UnavailableReason | None = None
    #: ``null`` without a value **or** without a threshold for this template.
    threshold_state: ThresholdLevel | None = None


class CurrentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    metrics: list[MetricReading]
    #: Only templates that have one. Absent is not zero.
    thresholds: dict[str, dict[str, float]]
    source: Literal["prometheus"] = "prometheus"
    demo_mode: bool


class MetricSeries(BaseModel):
    """One template's history (§15.2). ``points`` is ``null`` unless ``ok``."""

    model_config = ConfigDict(extra="forbid")

    template: str
    unit: str
    data_state: DataState
    unavailable_reason: UnavailableReason | None = None
    points: list[Point] | None = None


class RangeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    #: The window actually queried, echoed so the UI can show it (PRD §4.4).
    start: datetime
    end: datetime
    step_seconds: int
    series: list[MetricSeries]
    thresholds: dict[str, dict[str, float]]
    source: Literal["prometheus"] = "prometheus"
    demo_mode: bool


class CompareRequest(BaseModel):
    """§15.3's body. Exactly one of ``server_ids`` / ``environments``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    metric: str
    server_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=100)] | None = None
    environments: Annotated[list[Environment], Field(min_length=1, max_length=4)] | None = None
    from_: datetime = Field(alias="from")
    to: datetime
    step_seconds: int

    @model_validator(mode="after")
    def _one_selector(self) -> Self:
        if (self.server_ids is None) == (self.environments is None):
            raise ValueError("provide exactly one of server_ids or environments")
        return self


class ComparisonSeries(BaseModel):
    """One server's line in a comparison. Present even with no data."""

    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    display_name: str
    environment: Environment
    data_state: DataState
    unavailable_reason: UnavailableReason | None = None
    points: list[Point] | None = None


class MetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    unit: str
    start: datetime
    end: datetime
    step_seconds: int
    series: list[ComparisonSeries]
    thresholds: dict[str, float] | None = None
    #: Requested server ids the caller cannot read (or that do not exist —
    #: the two are indistinguishable by design). Always ``0`` for an
    #: environment selection, where the set is "what I may read in it".
    excluded_count: int
    source: Literal["prometheus"] = "prometheus"
    demo_mode: bool
