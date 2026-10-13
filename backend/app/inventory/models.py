"""Inventory: cloud accounts, servers, applications, services, action policies.

Columns marked "† beyond PRD §6" reconcile PRD §4.3's list of required server
attributes with PRD §6's shorter table definition (docs/DATABASE.md §4.2, §17).

``applications`` has no ``environment`` column (AD-12): PRD §4.9 lists it as an
application attribute, but PRD §6 omits it while putting ``environment`` on
``deployments``, and §4.9 also requires one application to span many servers.
**OPEN-02 — needs product confirmation before M1.5.**
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.approvals.models import ENVIRONMENTS
from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check

PROVIDERS: Final[tuple[str, ...]] = ("AWS", "AZURE", "ON_PREM", "OTHER")
ACCOUNT_STATUSES: Final[tuple[str, ...]] = ("ACTIVE", "DISABLED", "ERROR")
OPERATING_SYSTEMS: Final[tuple[str, ...]] = ("LINUX", "WINDOWS", "OTHER")
MANAGEMENT_METHODS: Final[tuple[str, ...]] = (
    "AWS_SSM",
    "AZURE_RUN_COMMAND",
    "AZURE_ARC",
    "RESTRICTED_AGENT",
    "NONE",
)
AGENT_STATUSES: Final[tuple[str, ...]] = (
    "HEALTHY",
    "DEGRADED",
    "UNREACHABLE",
    "UNKNOWN",
    "NOT_INSTALLED",
)
SERVER_STATUSES: Final[tuple[str, ...]] = (
    "PENDING",
    "MANAGED",
    "STALE",
    "UNREACHABLE",
    "RETIRED",
)
APPLICATION_STATUSES: Final[tuple[str, ...]] = ("ACTIVE", "ARCHIVED")
SERVICE_STATES: Final[tuple[str, ...]] = (
    "ACTIVE",
    "INACTIVE",
    "FAILED",
    "ACTIVATING",
    "DEACTIVATING",
    "UNCOLLECTED",
    "UNSUPPORTED",
    "UNKNOWN",
)
SERVICE_ACTIONS: Final[tuple[str, ...]] = (
    "start",
    "stop",
    "restart",
    "status",
    "logs_tail",
)

#: Defence in depth only. Registration in the ``services`` table — not this
#: pattern — is the control that stops an arbitrary unit being targeted
#: (docs/SECURITY.md §15).
SERVICE_NAME_PATTERN = r"^[A-Za-z0-9@._\-]{1,128}\.(service|socket|timer|target)$"


class CloudAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A provider account, subscription, or the container for non-cloud servers."""

    __tablename__ = "cloud_accounts"

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # An identifier (AWS account id, Azure subscription id, a site label).
    # Never a credential.
    account_reference: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    region_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # A secret-store path or role ARN — **never a secret value**
    # (docs/SECURITY.md §9). The API rejects anything secret-shaped here.
    credential_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # DISABLED immediately stops every operation against this account: a
    # containment control (docs/SECURITY.md §27).
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    last_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    __table_args__ = (
        enum_check("provider", PROVIDERS, "provider"),
        enum_check("status", ACCOUNT_STATUSES, "status"),
        Index(
            "uq_cloud_accounts_provider_account_reference",
            "provider",
            "account_reference",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Server(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "servers"

    # NOT NULL on purpose: see the uniqueness note in __table_args__.
    cloud_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cloud_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    provider_resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)

    operating_system: Mapped[str] = mapped_column(String(16), nullable=False)
    os_version: Mapped[str | None] = mapped_column(String(128), nullable=True)  # †

    # THE authorisation boundary. Read from this row, never from a request
    # (docs/SECURITY.md §4 rule 2).
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    private_ip: Mapped[ipaddress.IPv4Address | None] = mapped_column(  # †
        INET, nullable=True
    )
    owner_team: Mapped[str | None] = mapped_column(String(128), nullable=True)  # †

    management_method: Mapped[str] = mapped_column(String(32), nullable=False)
    monitoring_enabled: Mapped[bool] = mapped_column(  # †
        nullable=False, server_default=text("true")
    )
    # Must equal the Prometheus `instance` label for this host (AD-04).
    # Without it, metrics cannot be attributed to this server at all.
    metrics_instance: Mapped[str | None] = mapped_column(  # †
        String(256), nullable=True
    )
    agent_status: Mapped[str] = mapped_column(  # †
        String(16), nullable=False, server_default=text("'UNKNOWN'")
    )

    # Derived, not entered: the stale sweep owns MANAGED/STALE/UNREACHABLE,
    # administrators own PENDING/RETIRED (AD-14).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    # NULL means *never seen*, which is not the same as stale.
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(  # †
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        enum_check("operating_system", OPERATING_SYSTEMS, "operating_system"),
        enum_check("environment", ENVIRONMENTS, "environment"),
        enum_check("management_method", MANAGEMENT_METHODS, "management_method"),
        enum_check("agent_status", AGENT_STATUSES, "agent_status"),
        enum_check("status", SERVER_STATUSES, "status"),
        # PRD §4.3 acceptance: duplicate provider resource ids are detected
        # within their account. cloud_account_id is NOT NULL precisely so this
        # works — NULLs compare as distinct in a unique index, so a nullable
        # account column would silently permit the duplicates this forbids.
        Index(
            "uq_servers_cloud_account_id_provider_resource_id",
            "cloud_account_id",
            "provider_resource_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Two servers claiming one Prometheus instance would cross-attribute
        # every metric.
        Index(
            "uq_servers_metrics_instance",
            "metrics_instance",
            unique=True,
            postgresql_where=text("metrics_instance IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_servers_environment_status", "environment", "status"),
        Index(
            "ix_servers_status_active",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Drives the stale sweep (M2.5).
        Index("ix_servers_last_seen_at", "last_seen_at"),
        Index("ix_servers_cloud_account_id", "cloud_account_id"),
        Index("ix_servers_hostname_lower", text("lower(hostname)")),
    )


class Application(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "applications"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # URL-safe identifier; also the source of the Loki `application_id` label.
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    owner_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    repository_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Registered health-check endpoints per environment. Every URL here is
    # SSRF-validated at write time and again at request time
    # (docs/SECURITY.md §19).
    health_check_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))

    __table_args__ = (
        enum_check("status", APPLICATION_STATUSES, "status"),
        CheckConstraint(r"slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'", name="ck_applications_slug"),
        Index(
            "uq_applications_slug",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_applications_name",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ApplicationServer(Base):
    """Association only — carries no history, so CASCADE is safe here."""

    __tablename__ = "application_servers"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    role_on_server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    # PRD §4.9 both ways: one application on many servers, one server hosting
    # many applications.
    __table_args__ = (Index("ix_application_servers_server_id", "server_id"),)


class ServiceActionPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Which actions are permitted on the services bound to this policy.

    An addition beyond PRD §6, which defines ``services.action_policy_id`` but
    no table for it to reference, while PRD §4.6 requires "a configurable
    service allowlist" and action-specific permissions (docs/DATABASE.md §4.6).
    """

    __tablename__ = "service_action_policies"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # Empty array means read-only. This is the action allowlist
    # (docs/SECURITY.md §15).
    allowed_actions: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    approval_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_policies.id", ondelete="RESTRICT"), nullable=True
    )
    max_concurrent_operations: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    # Becomes operations.deadline_at, so a crashed worker's operation can still
    # be timed out correctly by the reaper.
    operation_timeout_seconds: Mapped[int] = mapped_column(
        nullable=False, server_default=text("300")
    )

    __table_args__ = (
        CheckConstraint(
            "allowed_actions <@ ARRAY['start','stop','restart','status','logs_tail']::varchar[]",
            name="ck_service_action_policies_allowed_actions",
        ),
        CheckConstraint(
            "max_concurrent_operations > 0",
            name="ck_service_action_policies_max_concurrent_operations",
        ),
        CheckConstraint(
            "operation_timeout_seconds BETWEEN 10 AND 3600",
            name="ck_service_action_policies_operation_timeout_seconds",
        ),
    )


class Service(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A registered systemd unit on a server.

    **This table is the service allowlist**: a unit with no row here can never
    be targeted by an operation (docs/SECURITY.md §15).
    """

    __tablename__ = "services"

    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    monitoring_enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    # NULL means **no actions permitted** — fail closed.
    action_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service_action_policies.id", ondelete="RESTRICT"), nullable=True
    )

    # A denormalised cache of Prometheus so list pages do not fan out per row.
    # Prometheus stays authoritative; a stale last_observed_at renders as stale
    # rather than as the last known good value presented as current (PRD §4.5).
    last_observed_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'UNKNOWN'")
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        enum_check("last_observed_state", SERVICE_STATES, "last_observed_state"),
        CheckConstraint(
            f"service_name ~ '{SERVICE_NAME_PATTERN}'", name="ck_services_service_name"
        ),
        Index(
            "uq_services_server_id_service_name",
            "server_id",
            "service_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_services_server_id", "server_id"),
        Index("ix_services_application_id", "application_id"),
        # Powers the dashboard's failed-services panel without scanning.
        Index(
            "ix_services_failed",
            "last_observed_state",
            postgresql_where=text("last_observed_state = 'FAILED'"),
        ),
    )
