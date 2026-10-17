"""Dashboard response models (docs/API_CONTRACTS.md §13).

**One source per section, one ``data_state`` per section.** That rule decides
the shape of this module, and it is the reason two blocks here are not laid
out exactly as §13.1's example payload draws them.

At M1.6 the product reads PostgreSQL and nothing else. Two sections therefore
have a real source and the rest have none:

* ``servers`` — counts of ``servers.status``, a real column. ``ok``.
* ``failed_services`` — a count over ``services``, by the state each row
  *shows* (M2.4), with the number of units that could not be read beside it.
  A zero over an empty registry is a real zero; a zero over unread units is
  not, and ``unobserved`` is what says which.
* everything else — ``not_configured`` with ``null`` values and a reason
  naming the milestone that will fill it.

§13.1's example draws ``healthy``, ``warning`` and ``critical`` inside the
``servers`` block beside ``total`` and ``managed``. They cannot live there
honestly: the first three are a *health* rollup with no source until M2.1,
the rest are counts of a stored column, and one envelope cannot carry one
``data_state`` for both. They are a sibling section, :class:`ServerHealthRollup`,
with the same field names. Recorded in docs/API_CONTRACTS.md §13.1.

For the same reason ``recent_deployments``, ``recent_operations`` and
``recent_health_changes`` are **sections carrying a null list**, not the bare
``[]`` the example shows. There is no deployments table, no operations table
and no health-event table; an empty array would say "nothing happened
recently", which is a claim this milestone cannot make. PRD §4.2's acceptance
criterion — "must not silently replace unavailable data with zero values" —
covers an empty list for the same reason it covers a zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.inventory.schemas import CursorPage, DataSection, HealthState

#: Which milestone each absent source is waiting for. Named here rather than
#: written at each call site so the dashboard cannot tell an operator two
#: different stories about the same missing integration.
UTILISATION_REASON = (
    "Fleet-wide utilisation is not aggregated yet; per-server metrics are on each server's page."
)
HEALTH_REASON = "Server health evaluation is not implemented yet (M2.5)."
ALERTS_REASON = "Alerting is not implemented yet (M3.3)."
DEPLOYMENTS_REASON = "Deployments are not implemented yet (M5.1)."
OPERATIONS_REASON = "Service operations are not implemented yet (M4.2)."
HEALTH_CHANGES_REASON = "Health-change history is not implemented yet (M2.5)."


class ServerCounts(DataSection):
    """Counts of ``servers.status`` over the caller's readable servers.

    Soft-deleted rows are excluded, as everywhere else in inventory, so
    ``total`` is the number of registered servers the caller can see.

    ``stale`` and ``unreachable`` are the stored statuses, which AD-14's sweep
    will recompute from ``last_seen_at`` in M2.5. Until then nothing moves a
    server into them automatically, so they report what registration and
    administration have set — an accurate count of a real column, not an
    observation of the host.
    """

    total: int
    pending: int
    managed: int
    stale: int
    unreachable: int
    retired: int


class ServerHealthRollup(DataSection):
    """The health rollup §13.1 draws inside ``servers``. No source until M2.1.

    Every count is ``null``. A zero here would read as "no critical servers",
    which is exactly the substitution PRD §4.2 forbids.
    """

    healthy: int | None = None
    warning: int | None = None
    critical: int | None = None
    unknown: int | None = None


class UtilisationSection(DataSection):
    cpu_percent_avg: float | None = None
    memory_percent_avg: float | None = None
    disk_percent_max: float | None = None
    network_bytes_per_second: float | None = None


class FailedServicesSection(DataSection):
    """Units that *show* ``FAILED``, and how many could not be read at all.

    ``count`` is a real count over the registry (``services`` is the allowlist),
    but it is a count of what is **known**: a unit whose host is silent, or whose
    exporter reports nothing, shows ``UNKNOWN`` or ``UNCOLLECTED`` and is not in
    it. ``unobserved`` is how many such units there are, so ``0`` failed beside
    ``12`` unobserved cannot be mistaken for ``0`` failed of ``12``.
    """

    count: int | None = None
    unobserved: int | None = None


class ActiveAlertsSection(DataSection):
    critical: int | None = None
    warning: int | None = None
    info: int | None = None


class RecentActivitySection(DataSection):
    """A recent-activity list whose table does not exist yet.

    ``items`` is ``null``, never ``[]``: "we have no record" and "nothing
    happened" are different answers and the operator has to be able to tell
    them apart.
    """

    items: list[dict[str, object]] | None = None


class DashboardFilters(BaseModel):
    """The filters actually applied, echoed back (§13.1 ``filters_applied``)."""

    model_config = ConfigDict(extra="forbid")

    environment: list[str] | None = None
    provider: list[str] | None = None
    region: list[str] | None = None
    application_id: list[uuid.UUID] | None = None
    operating_system: list[str] | None = None
    owner_team: list[str] | None = None


class DashboardSummary(BaseModel):
    """§13.1."""

    model_config = ConfigDict(extra="forbid")

    servers: ServerCounts
    server_health: ServerHealthRollup
    utilisation: UtilisationSection
    failed_services: FailedServicesSection
    active_alerts: ActiveAlertsSection
    recent_deployments: RecentActivitySection
    recent_operations: RecentActivitySection
    recent_health_changes: RecentActivitySection
    filters_applied: DashboardFilters
    demo_mode: bool
    #: How long this payload may be served from cache. Surfaced so the client
    #: does not have to guess how often polling is worth anything.
    cache_ttl_seconds: int


class ServerHealthRow(BaseModel):
    """One row of §13.2's status table."""

    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    display_name: str
    hostname: str
    environment: str
    provider: str
    status: str
    #: ``null`` at this milestone and never defaulted to ``healthy``: no
    #: collector has looked at this host. The list's ``health`` section says
    #: why, once, rather than repeating the reason on every row.
    health_state: HealthState | None
    last_seen_at: datetime | None
    #: ``null`` when the host has never been seen — that is not staleness.
    stale: bool | None
    #: Real: a count of rows in ``services``.
    failed_service_count: int
    #: ``null`` until there is an alerts table (M3.3).
    active_alert_count: int | None


class DashboardHealthList(BaseModel):
    """§13.2. Cursor-paged, scope-filtered, with the honest sections beside it."""

    model_config = ConfigDict(extra="forbid")

    items: list[ServerHealthRow]
    page: CursorPage
    #: Why every row's ``health_state`` is ``null``.
    health: ServerHealthRollup
    #: Why every row's ``active_alert_count`` is ``null``.
    alerts: ActiveAlertsSection
    collected_at: datetime
    filters_applied: DashboardFilters
    demo_mode: bool
    cache_ttl_seconds: int


HealthStatusFilter = Literal["healthy", "warning", "critical", "stale", "unreachable", "unknown"]
