"""Unit tests for the db layer: no database required."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from app.audit.models import ACTOR_TYPES, OUTCOMES, AuditEvent
from app.authz.models import ROLE_NAMES
from app.db.base import Base, all_models
from app.db.partitions import (
    create_partition_sql,
    months_from,
    partition_name,
)
from app.db.types import uuid7
from app.inventory.models import SERVICE_STATES


class TestUuid7:
    def test_is_a_valid_version_7_uuid(self) -> None:
        value = uuid7()
        assert isinstance(value, uuid.UUID)
        assert value.version == 7
        assert value.variant == uuid.RFC_4122

    def test_is_time_ordered(self) -> None:
        """The property the choice exists for: later ids sort later."""
        first = uuid7()
        # Cross a millisecond boundary; ordering within one ms is not claimed.
        while (second := uuid7()).int >> 80 == first.int >> 80:
            pass
        assert second > first

    def test_embedded_timestamp_is_now(self) -> None:
        before = datetime.now(UTC).timestamp() * 1000
        timestamp_ms = uuid7().int >> 80
        after = datetime.now(UTC).timestamp() * 1000
        assert before - 1000 <= timestamp_ms <= after + 1000

    def test_ids_are_unique_in_bulk(self) -> None:
        assert len({uuid7() for _ in range(10_000)}) == 10_000


class TestPartitionHelpers:
    def test_partition_name_format(self) -> None:
        assert partition_name(date(2026, 1, 15)) == "audit_events_2026_01"

    def test_months_from_crosses_a_year_boundary(self) -> None:
        months = months_from(date(2026, 11, 20), 4)
        assert [partition_name(m) for m in months] == [
            "audit_events_2026_11",
            "audit_events_2026_12",
            "audit_events_2027_01",
            "audit_events_2027_02",
        ]

    def test_months_from_normalises_to_the_first_of_the_month(self) -> None:
        assert months_from(date(2026, 3, 31), 1) == [date(2026, 3, 1)]

    def test_february_is_handled(self) -> None:
        """The +28/+4 day trick must not skip a short month."""
        months = months_from(date(2026, 1, 31), 3)
        assert [m.isoformat() for m in months] == [
            "2026-01-01",
            "2026-02-01",
            "2026-03-01",
        ]

    def test_partition_sql_bounds_are_half_open_and_contiguous(self) -> None:
        """Adjacent partitions must meet exactly: no gap, no overlap."""
        january = create_partition_sql(date(2026, 1, 1))
        february = create_partition_sql(date(2026, 2, 1))
        assert "FROM ('2026-01-01') TO ('2026-02-01')" in january
        assert "FROM ('2026-02-01') TO ('2026-03-01')" in february

    def test_partition_sql_is_idempotent(self) -> None:
        assert "IF NOT EXISTS" in create_partition_sql(date(2026, 1, 1))


class TestModelMetadata:
    def test_every_model_is_registered(self) -> None:
        names = {model.__name__ for model in all_models()}
        assert names == {
            "User",
            "Role",
            "Permission",
            "RolePermission",
            "UserRole",
            "CloudAccount",
            "Server",
            "Application",
            "ApplicationServer",
            "Service",
            "ServiceActionPolicy",
            "ApprovalPolicy",
            "AuditEvent",
            # M3.3: the alert lifecycle and its one-open-row arbiter (OPEN-42).
            "AlertEvent",
            "AlertOpenKey",
            # M3.4: the notification outbox and the destinations it delivers to.
            "NotificationChannel",
            "NotificationDelivery",
            # M4.2: the record of every privileged service operation.
            "Operation",
            # M4.5: one row per approval request (OPEN-94).
            "Approval",
            # M1.4: the durable read-only switch (docs/SECURITY.md §27).
            "SystemSetting",
        }

    def test_constraint_naming_convention_is_applied(self) -> None:
        """Anonymous constraint names make later migrations unreliable."""
        for table in Base.metadata.tables.values():
            for constraint in table.constraints:
                assert constraint.name is not None, f"{table.name} has an unnamed constraint"

    @pytest.mark.security
    def test_users_table_has_no_password_column(self) -> None:
        """PRD §4.1: authentication is entirely external. Asserted, not assumed."""
        columns = set(Base.metadata.tables["users"].columns.keys())
        forbidden = {"password", "password_hash", "passwd", "secret", "hashed_password"}
        assert columns & forbidden == set()

    @pytest.mark.security
    def test_audit_events_has_no_update_or_delete_affordance(self) -> None:
        """AD-11: the absence of these columns is the design, not an omission."""
        columns = set(Base.metadata.tables["audit_events"].columns.keys())
        assert "updated_at" not in columns
        assert "deleted_at" not in columns

    def test_audit_events_is_partitioned_by_occurred_at(self) -> None:
        table = Base.metadata.tables["audit_events"]
        assert table.kwargs.get("postgresql_partition_by") == "RANGE (occurred_at)"

    def test_audit_events_primary_key_includes_the_partition_key(self) -> None:
        """PostgreSQL requires it; without it the table cannot be created."""
        pk = {column.name for column in Base.metadata.tables["audit_events"].primary_key}
        assert pk == {"id", "occurred_at"}

    @pytest.mark.parametrize(
        "table",
        [
            "servers",
            "services",
            "applications",
            "cloud_accounts",
        ],
    )
    def test_soft_deletable_tables_have_partial_unique_indexes(self, table: str) -> None:
        """A unique index that ignores deleted_at makes a name unreusable.

        Every unique index on a soft-deletable table must be partial. The
        *identity* ones must be partial on ``deleted_at IS NULL`` specifically,
        or a hostname, slug or provider resource id can never be reused after
        a retire — which `test_duplicate_detection.py` exercises end to end.

        The idempotency indexes added by migration 0004 are the deliberate
        exception, and the opposite rule applies to them: a key must stay
        claimed *after* the row is soft-deleted, because a replay of the
        original create has to keep returning the original row rather than
        registering a second server (docs/API_CONTRACTS.md §7). They are
        partial on their own two columns instead, for the NULL-distinctness
        reason in docs/DATABASE.md §4.2.
        """
        indexes = Base.metadata.tables[table].indexes
        unique = [index for index in indexes if index.unique]
        assert unique, f"{table} has no unique index"
        identity = [index for index in unique if not str(index.name).endswith("_idempotency_key")]
        assert identity, f"{table} has no identity unique index"
        for index in unique:
            where = index.dialect_options["postgresql"].get("where")
            assert where is not None, f"{index.name} is unique but not partial"
            if index in identity:
                assert "deleted_at IS NULL" in str(where)
            else:
                assert "idempotency_key IS NOT NULL" in str(where)
                assert "deleted_at" not in str(where), (
                    f"{index.name} must survive a soft delete, or a replayed "
                    "Idempotency-Key would create a second row"
                )

    @pytest.mark.parametrize(
        "table",
        ["deployments"],
    )
    def test_history_tables_are_not_created_early(self, table: str) -> None:
        """These arrive with the milestones that write to them (M5).

        ``alert_events`` left this list when M3.3 created it, ``notification_deliveries`` when
        M3.4 did, ``operations`` when M4.2 did and ``approvals`` when M4.5 did.
        """
        assert table not in Base.metadata.tables


class TestEnumSets:
    def test_role_names_match_the_five_prd_roles(self) -> None:
        assert set(ROLE_NAMES) == {
            "VIEWER",
            "DEVELOPER",
            "DEVOPS_ENGINEER",
            "APPROVER",
            "ADMINISTRATOR",
        }

    def test_service_states_match_the_api_contract(self) -> None:
        """docs/API_CONTRACTS.md §9 ServiceState is a closed enum."""
        assert set(SERVICE_STATES) == {
            "ACTIVE",
            "INACTIVE",
            "FAILED",
            "ACTIVATING",
            "DEACTIVATING",
            "UNCOLLECTED",
            "UNSUPPORTED",
            "UNKNOWN",
        }

    def test_actor_types_allow_non_human_identities(self) -> None:
        """PRD §4.12 says "actor **or service identity**"."""
        assert set(ACTOR_TYPES) == {"USER", "SYSTEM", "INTEGRATION"}

    def test_outcomes_include_denied(self) -> None:
        """Authorisation failures are audited, so DENIED must be representable."""
        assert "DENIED" in OUTCOMES

    def test_audit_event_defaults_generate_an_id(self) -> None:
        event = AuditEvent(
            actor_type="SYSTEM",
            actor_label="system:test",
            action_type="TEST",
            correlation_id=uuid.uuid4(),
            outcome="SUCCESS",
        )
        # default= is applied at flush time by SQLAlchemy, so check the callable.
        assert Base.metadata.tables["audit_events"].columns["id"].default is not None
        assert event.actor_label == "system:test"
