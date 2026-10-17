"""Request and response models for server and application inventory (§14, §18).

``extra="forbid"`` on every request model, as elsewhere: a silently ignored
field in a registration request produces a row the operator did not ask for and
believes they did.

Three things this module is careful about, each because the contracts are
explicit and each easy to get subtly wrong:

* **Immutable fields are absent from the update models, not filtered later.**
  ``ServerUpdateRequest`` has no ``cloud_account_id``, so sending one is
  ``422`` with ``unknown_field`` from ``extra="forbid"`` — the refusal §14.4
  requires, produced by the model rather than by a check somebody has to
  remember to write.
* **Honest states.** ``null`` is unknown and is never rendered as ``0``
  (CLAUDE.md §5). The sections that have no source yet — health, open alerts,
  provider capabilities — say ``not_configured`` and carry ``null``, rather
  than a zero that reads as "none".
* **No value is echoed back in a validation message** (§8). The validators here
  name the rule that failed.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.approvals.models import ENVIRONMENTS
from app.core.ssrf import UrlNotAllowedError, validate_registered_url
from app.inventory.models import (
    APPLICATION_STATUSES,
    MANAGEMENT_METHODS,
    OPERATING_SYSTEMS,
    SERVER_STATUSES,
)
from app.monitoring.health import HealthState

Environment = Literal["development", "staging", "production", "other"]
OperatingSystem = Literal["LINUX", "WINDOWS", "OTHER"]
ManagementMethod = Literal["AWS_SSM", "AZURE_RUN_COMMAND", "AZURE_ARC", "RESTRICTED_AGENT", "NONE"]
ServerStatus = Literal["PENDING", "MANAGED", "STALE", "UNREACHABLE", "RETIRED"]
#: §14.4: an administrator may move a server between these three only. The
#: other two (``STALE``, ``UNREACHABLE``) are derived by the stale sweep
#: (AD-14) and setting them by hand would be asserting an observation.
SettableServerStatus = Literal["PENDING", "MANAGED", "RETIRED"]
ApplicationStatus = Literal["ACTIVE", "ARCHIVED"]
DataState = Literal["ok", "stale", "unavailable", "unsupported", "not_configured"]

# Same guard as app/admin/schemas.py: the Literals above and the database CHECK
# constraints are two statements of one closed set, and a mismatch means the
# API accepts what the database will reject. Raised, not asserted — `python -O`
# strips an assert.
if (
    set(OPERATING_SYSTEMS) != set(get_args(OperatingSystem))
    or set(MANAGEMENT_METHODS) != set(get_args(ManagementMethod))
    or set(SERVER_STATUSES) != set(get_args(ServerStatus))
    or set(APPLICATION_STATUSES) != set(get_args(ApplicationStatus))
    or set(ENVIRONMENTS) != set(get_args(Environment))
):
    raise RuntimeError(
        "inventory schema enums have drifted from the database CHECK constraints; "
        "update app/inventory/schemas.py and the migration together"
    )

#: A DNS name: labels of letters, digits and hyphens, no leading or trailing
#: hyphen, optional trailing dot. Length is bounded separately because the
#: 253-character limit is on the whole name, not per label.
_HOSTNAME_LABEL: Final = r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
_HOSTNAME_PATTERN: Final = re.compile(rf"^{_HOSTNAME_LABEL}(\.{_HOSTNAME_LABEL})*\.?$")

#: ``host:port`` — the Prometheus ``instance`` label's form (AD-04). Validated
#: because a mismatch here does not fail loudly: metrics simply never attach to
#: the server, which looks like a monitoring gap rather than a typo.
#:
#: Public because ``app/metrics/templates.py`` re-applies it before putting the
#: value into a PromQL selector. Validating once at the write boundary is where
#: a typo gets a useful error message; validating again at the point of
#: interpolation is what makes the renderer safe regardless of how the row got
#: there (a seed, a migration, a direct UPDATE).
_METRICS_INSTANCE_HOST: Final = (
    rf"(?:{_HOSTNAME_LABEL}(\.{_HOSTNAME_LABEL})*"  # DNS name
    r"|\[[0-9A-Fa-f:]+\]"  # bracketed IPv6
    r"|(?:\d{1,3}\.){3}\d{1,3})"  # IPv4
)
#: ``\Z``, not ``$``: in Python ``$`` also matches immediately before a
#: trailing newline, so a value ending in one satisfied a ``$``-anchored
#: pattern.
#: Harmless at the write boundary (it is a typo that gets stored), but this
#: value is interpolated into a PromQL string literal, and a validator that
#: accepts a trailing control character is not the guard the renderer is
#: relying on. The sibling patterns in this module have the same slip and are
#: recorded in PROGRESS.md rather than changed here (CLAUDE.md §3).
METRICS_INSTANCE_PATTERN: Final = re.compile(rf"^{_METRICS_INSTANCE_HOST}:\d{{1,5}}\Z")

#: ``org/repo``, the non-URL form §18.2 permits.
_ORG_REPO_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$")

#: Per-provider shape for ``provider_resource_id`` (§14.2 "matching the
#: provider's expected pattern"). Checked in the service layer, which is where
#: the account — and therefore the provider — is known; a request model cannot
#: see it. ``OTHER`` is deliberately permissive: it exists for providers this
#: product has not modelled, so constraining its ids would be guessing.
PROVIDER_RESOURCE_ID_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "AWS": re.compile(r"^i-[0-9a-f]{8,32}$"),
    "AZURE": re.compile(r"^/subscriptions/[^/\s]{1,128}(/[^/\s]{1,128})+$"),
    "ON_PREM": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"),
    "OTHER": re.compile(r"^\S{1,256}$"),
}

#: Which management methods each provider can actually carry (§14.2:
#: "``AZURE_RUN_COMMAND`` on an AWS account ⇒ ``422``"), from the capability
#: matrix in docs/ARCHITECTURE.md §7.2. ``NONE`` is always permitted: it means
#: monitoring only, which every provider supports by doing nothing.
PROVIDER_MANAGEMENT_METHODS: Final[dict[str, frozenset[str]]] = {
    "AWS": frozenset({"AWS_SSM", "RESTRICTED_AGENT", "NONE"}),
    "AZURE": frozenset({"AZURE_RUN_COMMAND", "AZURE_ARC", "RESTRICTED_AGENT", "NONE"}),
    "ON_PREM": frozenset({"RESTRICTED_AGENT", "NONE"}),
    "OTHER": frozenset({"RESTRICTED_AGENT", "NONE"}),
}

Hostname = Annotated[str, Field(min_length=1, max_length=253)]
DisplayName = Annotated[str, Field(min_length=1, max_length=256)]
OwnerTeam = Annotated[str, Field(min_length=1, max_length=128)]
Slug = Annotated[str, Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]


def _check_hostname(value: str | None) -> str | None:
    if value is None:
        return None
    if not _HOSTNAME_PATTERN.match(value):
        raise ValueError("must be a valid DNS name")
    return value


def _check_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("must be a valid IPv4 or IPv6 address") from error
    return value


def _check_metrics_instance(value: str | None) -> str | None:
    if value is None:
        return None
    if not METRICS_INSTANCE_PATTERN.match(value):
        raise ValueError("must be host:port, matching the Prometheus instance label")
    return value


def _check_repository_reference(value: str | None) -> str | None:
    """§18.2: an ``https`` URL on the allowlist, or an ``org/repo`` form."""
    if value is None:
        return None
    if "://" in value:
        try:
            return validate_registered_url(value)
        except UrlNotAllowedError as error:
            raise ValueError(str(error)) from error
    if not _ORG_REPO_PATTERN.match(value):
        raise ValueError('must be an allowlisted https URL or an "org/repo" reference')
    return value


# --- shared sections ---------------------------------------------------------


class DataSection(BaseModel):
    """The §4 partial-data envelope, for a section with no value to give.

    ``value`` is absent rather than ``null``-and-meaningless: §4 rule 2 says
    ``unavailable`` and ``stale`` never carry substitute values, and the
    subclasses below spell out what the missing value *would* have been.
    """

    model_config = ConfigDict(extra="forbid")

    data_state: DataState
    collected_at: datetime | None = None
    source: str | None = None
    stale_after_seconds: int | None = None
    unavailable_reason: str | None = None


class HealthSection(DataSection):
    #: ``null`` whenever ``data_state`` is not ``ok``/``stale``. Never
    #: "healthy" by default — an unmonitored host is unknown, not well.
    state: HealthState | None = None


class OpenAlertCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critical: int
    warning: int


class OpenAlertsSection(DataSection):
    counts: OpenAlertCounts | None = None


class ManagementCapabilitiesSection(DataSection):
    """§14.3's "explicit statement of what this provider can and cannot do".

    Reported as ``not_configured`` until a provider adapter exists (M4.1).
    docs/ARCHITECTURE.md §7.2 documents what each provider *will* support, but
    publishing that table as though it had been read from a configured account
    would present an unimplemented integration as operational (CLAUDE.md §7).
    """

    capabilities: dict[str, bool] | None = None


class AssociatedApplication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    slug: str
    role_on_server: str | None


class AssociatedServer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    hostname: str
    display_name: str
    environment: str
    status: str
    role_on_server: str | None


class CursorPage(BaseModel):
    """§5's cursor page object.

    ``total`` is ``null`` by design and is typed as such rather than as an
    optional integer that might one day be filled: §5 gives two reasons — the
    count is a latency trap on a large table, and on scope-filtered data a
    total invites inference about the rows the caller cannot see.
    """

    model_config = ConfigDict(extra="forbid")

    limit: int
    next_cursor: str | None
    has_more: bool
    total: None = None


# --- servers -----------------------------------------------------------------


class ServerSummary(BaseModel):
    """A list row (§14.1). The stored columns, with nothing derived."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    cloud_account_id: uuid.UUID
    provider_resource_id: str
    hostname: str
    display_name: str
    operating_system: str
    os_version: str | None
    environment: str
    region: str | None
    private_ip: str | None
    owner_team: str | None
    management_method: str
    monitoring_enabled: bool
    metrics_instance: str | None
    agent_status: str
    status: str
    last_seen_at: datetime | None
    registered_at: datetime
    created_at: datetime
    updated_at: datetime


class ServerDetail(ServerSummary):
    """§14.3: the full row plus the derived and related parts."""

    provider_metadata: dict[str, Any]
    #: ``null`` when ``last_seen_at`` is ``null`` — never seen is not stale,
    #: and reporting ``false`` would claim a freshness nothing has observed.
    stale: bool | None
    health: HealthSection
    applications: list[AssociatedApplication]
    #: Registered services, which is a count of rows in this database and so
    #: is a real number even at this milestone.
    registered_service_count: int
    open_alerts: OpenAlertsSection
    management_capabilities: ManagementCapabilitiesSection


class ServerCreated(ServerDetail):
    """The create response (§14.2, §7).

    ``idempotent_replay`` is on the create response only, not on
    :class:`ServerDetail`: it is a fact about *this request*, and a ``GET``
    that carried it would be answering a question nobody asked.
    """

    idempotent_replay: bool = False


class ServerList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ServerSummary]
    page: CursorPage
    demo_mode: bool


class ServerCreateRequest(BaseModel):
    """§14.2. ``status`` is absent on purpose: a new server is ``PENDING``.

    PRD §4.3 — "registration grants no operational permission" — so the status
    is set by the server, not chosen by the requester.
    """

    model_config = ConfigDict(extra="forbid")

    cloud_account_id: uuid.UUID
    provider_resource_id: Annotated[str, Field(min_length=1, max_length=256)]
    hostname: Hostname
    display_name: DisplayName
    operating_system: OperatingSystem
    os_version: Annotated[str | None, Field(default=None, min_length=1, max_length=128)] = None
    environment: Environment
    region: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None
    private_ip: Annotated[str | None, Field(default=None, max_length=45)] = None
    owner_team: OwnerTeam | None = None
    management_method: ManagementMethod
    monitoring_enabled: bool = True
    metrics_instance: Annotated[str | None, Field(default=None, max_length=256)] = None
    #: Associations created with the server (§14.2). Bounded, because an
    #: unbounded list here is an unbounded transaction.
    application_ids: Annotated[list[uuid.UUID], Field(max_length=64)] = []

    _hostname = field_validator("hostname")(_check_hostname)
    _private_ip = field_validator("private_ip")(_check_ip)
    _metrics_instance = field_validator("metrics_instance")(_check_metrics_instance)


class ServerUpdateRequest(BaseModel):
    """§14.4 — mutable fields only.

    The immutable ones (``id``, ``cloud_account_id``, ``provider_resource_id``,
    ``created_at``) are simply not declared, so ``extra="forbid"`` answers
    ``422`` for them. ``hostname`` is immutable too: it is the identity a
    metrics target and a log stream are attributed by, and renaming it would
    silently re-point history.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    owner_team: OwnerTeam | None = None
    environment: Environment | None = None
    monitoring_enabled: bool | None = None
    metrics_instance: Annotated[str | None, Field(default=None, max_length=256)] = None
    status: SettableServerStatus | None = None
    os_version: Annotated[str | None, Field(default=None, min_length=1, max_length=128)] = None
    private_ip: Annotated[str | None, Field(default=None, max_length=45)] = None
    region: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None

    _private_ip = field_validator("private_ip")(_check_ip)
    _metrics_instance = field_validator("metrics_instance")(_check_metrics_instance)


class AssociateApplicationRequest(BaseModel):
    """§14.6."""

    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    role_on_server: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None


# --- applications ------------------------------------------------------------


class HealthCheckEntry(BaseModel):
    """One registered health-check endpoint.

    §18.2 names ``health_check_config`` and §18.6 shows the checks it drives,
    but neither pins the stored shape, so this is the shape — recorded as an
    assumption in PROGRESS.md rather than invented silently. Every ``url`` is
    allowlist-validated here at write time (docs/SECURITY.md §19); the
    request-time check lands with the runner that first fetches one.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=64)]
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    #: Which environment this check belongs to. Applications are
    #: environment-agnostic (AD-12); their *checks* are not, because the
    #: staging endpoint is a different URL from the production one.
    environment: Environment
    expected_status: Annotated[int, Field(default=200, ge=100, le=599)] = 200

    @field_validator("url")
    @classmethod
    def _allowlisted(cls, value: str) -> str:
        try:
            return validate_registered_url(value)
        except UrlNotAllowedError as error:
            raise ValueError(str(error)) from error


class HealthCheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: Annotated[list[HealthCheckEntry], Field(max_length=16)] = []


class ApplicationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    owner_team: str | None
    repository_reference: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationSummary):
    """§18.3: "servers, services, targets".

    ``deployment_targets`` has no rows and no endpoint until M5.1, so the field
    is reported as an explicitly not-configured section rather than as an empty
    list — "none registered" and "this product cannot tell you yet" are
    different answers (CLAUDE.md §5).
    """

    health_check_config: HealthCheckConfig
    servers: list[AssociatedServer]
    registered_service_count: int
    deployment_targets: DataSection


class ApplicationCreated(ApplicationDetail):
    """See :class:`ServerCreated`."""

    idempotent_replay: bool = False


class ApplicationList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApplicationSummary]
    page: CursorPage
    demo_mode: bool


class ApplicationCreateRequest(BaseModel):
    """§18.2."""

    model_config = ConfigDict(extra="forbid")

    name: DisplayName
    slug: Slug
    description: Annotated[str | None, Field(default=None, min_length=1, max_length=2048)] = None
    owner_team: OwnerTeam | None = None
    repository_reference: Annotated[str | None, Field(default=None, max_length=512)] = None
    health_check_config: HealthCheckConfig = HealthCheckConfig()

    _repository = field_validator("repository_reference")(_check_repository_reference)


class ApplicationUpdateRequest(BaseModel):
    """§18.4. ``slug`` is immutable: it is the Loki ``application_id`` label
    source, so changing it would orphan every log line already shipped."""

    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    description: Annotated[str | None, Field(default=None, min_length=1, max_length=2048)] = None
    owner_team: OwnerTeam | None = None
    repository_reference: Annotated[str | None, Field(default=None, max_length=512)] = None
    health_check_config: HealthCheckConfig | None = None
    status: ApplicationStatus | None = None

    _repository = field_validator("repository_reference")(_check_repository_reference)
