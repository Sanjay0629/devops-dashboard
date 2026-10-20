"""The constraints that carry security or correctness meaning.

Each test here corresponds to a PRD acceptance criterion or a documented
invariant, and each asserts that **the database** enforces it — not that the
application remembers to.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

ACCOUNT_SQL = text(
    """
    INSERT INTO cloud_accounts (id, provider, account_reference, display_name,
                                region_configuration, status, created_at, updated_at)
    VALUES (:id, :provider, :ref, 'Placeholder account', '{}'::jsonb, 'ACTIVE',
            now(), now())
    """
)

SERVER_SQL = text(
    """
    INSERT INTO servers (id, cloud_account_id, provider_resource_id, hostname,
                         display_name, operating_system, environment,
                         management_method, monitoring_enabled, agent_status,
                         status, registered_at, provider_metadata,
                         metrics_instance, created_at, updated_at)
    VALUES (:id, :account_id, :resource_id, :hostname, 'Placeholder', 'LINUX',
            :environment, 'AWS_SSM', true, 'UNKNOWN', 'PENDING', now(),
            '{}'::jsonb, :metrics_instance, now(), now())
    """
)


def _account(connection: Connection, reference: str = "000000000000") -> uuid.UUID:
    account_id = uuid.uuid4()
    connection.execute(ACCOUNT_SQL, {"id": account_id, "provider": "AWS", "ref": reference})
    return account_id


def _server(
    connection: Connection,
    account_id: uuid.UUID,
    *,
    resource_id: str = "i-placeholder0000001",
    hostname: str = "placeholder.internal.example",
    environment: str = "staging",
    metrics_instance: str | None = None,
) -> uuid.UUID:
    server_id = uuid.uuid4()
    connection.execute(
        SERVER_SQL,
        {
            "id": server_id,
            "account_id": account_id,
            "resource_id": resource_id,
            "hostname": hostname,
            "environment": environment,
            "metrics_instance": metrics_instance,
        },
    )
    return server_id


class TestServerUniqueness:
    def test_duplicate_provider_resource_id_in_one_account_is_rejected(
        self, connection: Connection
    ) -> None:
        """PRD §4.3 acceptance criterion, enforced by the database."""
        account_id = _account(connection)
        _server(connection, account_id)
        with pytest.raises(IntegrityError):
            _server(connection, account_id, hostname="other.internal.example")

    def test_same_resource_id_in_a_different_account_is_allowed(
        self, connection: Connection
    ) -> None:
        """The criterion is scoped to the account, not global."""
        first = _account(connection, "000000000001")
        second = _account(connection, "000000000002")
        _server(connection, first)
        _server(connection, second, hostname="other.internal.example")

    def test_resource_id_can_be_reused_after_soft_delete(self, connection: Connection) -> None:
        """Why every unique index on these tables is partial."""
        account_id = _account(connection)
        server_id = _server(connection, account_id)
        connection.execute(
            text("UPDATE servers SET deleted_at = now() WHERE id = :id"),
            {"id": server_id},
        )
        _server(connection, account_id, hostname="rebuilt.internal.example")

    def test_two_servers_cannot_claim_one_prometheus_instance(self, connection: Connection) -> None:
        """Otherwise every metric is cross-attributed (AD-04)."""
        account_id = _account(connection)
        _server(connection, account_id, metrics_instance="placeholder:9100")
        with pytest.raises(IntegrityError):
            _server(
                connection,
                account_id,
                resource_id="i-placeholder0000002",
                hostname="second.internal.example",
                metrics_instance="placeholder:9100",
            )

    def test_null_metrics_instance_does_not_collide(self, connection: Connection) -> None:
        """Most servers have none; NULLs must not conflict with each other."""
        account_id = _account(connection)
        _server(connection, account_id, metrics_instance=None)
        _server(
            connection,
            account_id,
            resource_id="i-placeholder0000002",
            hostname="second.internal.example",
            metrics_instance=None,
        )


class TestEnumChecks:
    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("environment", "prod"),
            ("environment", "PRODUCTION"),
            ("operating_system", "linux"),
            ("status", "DELETED"),
            ("management_method", "SSH"),
            ("agent_status", "OK"),
        ],
    )
    def test_invalid_enum_value_is_rejected(
        self, connection: Connection, column: str, value: str
    ) -> None:
        account_id = _account(connection)
        server_id = _server(connection, account_id)
        with pytest.raises(IntegrityError):
            connection.execute(
                text(f"UPDATE servers SET {column} = :value WHERE id = :id"),  # noqa: S608
                {"value": value, "id": server_id},
            )

    def test_service_name_pattern_is_enforced(self, connection: Connection) -> None:
        """Defence in depth behind the registration allowlist."""
        account_id = _account(connection)
        server_id = _server(connection, account_id)
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO services (id, server_id, service_name,
                                          monitoring_enabled, last_observed_state,
                                          created_at, updated_at)
                    VALUES (:id, :server_id, 'rm -rf /', true, 'UNKNOWN',
                            now(), now())
                    """
                ),
                {"id": uuid.uuid4(), "server_id": server_id},
            )

    def test_application_slug_pattern_is_enforced(self, connection: Connection) -> None:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO applications (id, name, slug, health_check_config,
                                              status, created_at, updated_at)
                    VALUES (:id, 'Placeholder', 'Not A Slug', '{}'::jsonb, 'ACTIVE',
                            now(), now())
                    """
                ),
                {"id": uuid.uuid4()},
            )


class TestPolicyConstraints:
    def test_required_approvals_is_bounded(self, connection: Connection) -> None:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO approval_policies (id, name, environment, applies_to,
                                                   required_approvals,
                                                   allow_self_approval, required_role,
                                                   approval_window_seconds,
                                                   created_at, updated_at)
                    VALUES (:id, 'placeholder', 'production', 'DEPLOYMENT', 99,
                            false, 'APPROVER', 3600, now(), now())
                    """
                ),
                {"id": uuid.uuid4()},
            )

    def test_allowed_actions_must_be_a_subset_of_the_known_actions(
        self, connection: Connection
    ) -> None:
        """An unknown action must not be storable, let alone executable."""
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO service_action_policies (id, name, allowed_actions,
                                                         max_concurrent_operations,
                                                         operation_timeout_seconds,
                                                         created_at, updated_at)
                    VALUES (:id, 'placeholder', ARRAY['exec']::varchar[], 1, 300,
                            now(), now())
                    """
                ),
                {"id": uuid.uuid4()},
            )

    def test_service_with_no_action_policy_is_allowed_and_means_no_actions(
        self, connection: Connection
    ) -> None:
        """Fail closed: NULL policy is representable and grants nothing."""
        account_id = _account(connection)
        server_id = _server(connection, account_id)
        connection.execute(
            text(
                """
                INSERT INTO services (id, server_id, service_name, action_policy_id,
                                      monitoring_enabled, last_observed_state,
                                      created_at, updated_at)
                VALUES (:id, :server_id, 'placeholder-api.service', NULL, true,
                        'UNKNOWN', now(), now())
                """
            ),
            {"id": uuid.uuid4(), "server_id": server_id},
        )


class TestReferentialIntegrity:
    def test_server_requires_an_existing_cloud_account(self, connection: Connection) -> None:
        with pytest.raises(IntegrityError):
            _server(connection, uuid.uuid4())

    def test_cloud_account_with_servers_cannot_be_deleted(self, connection: Connection) -> None:
        """ON DELETE RESTRICT: deleting an account must not orphan servers."""
        account_id = _account(connection)
        _server(connection, account_id)
        with pytest.raises(IntegrityError):
            connection.execute(
                text("DELETE FROM cloud_accounts WHERE id = :id"), {"id": account_id}
            )

    def test_deleting_a_server_cascades_to_its_services(self, connection: Connection) -> None:
        """Pure associations and child rows may cascade; history may not."""
        account_id = _account(connection)
        server_id = _server(connection, account_id)
        connection.execute(
            text(
                """
                INSERT INTO services (id, server_id, service_name, monitoring_enabled,
                                      last_observed_state, created_at, updated_at)
                VALUES (:id, :server_id, 'placeholder-api.service', true, 'UNKNOWN',
                        now(), now())
                """
            ),
            {"id": uuid.uuid4(), "server_id": server_id},
        )
        connection.execute(text("DELETE FROM servers WHERE id = :id"), {"id": server_id})
        remaining = connection.execute(
            text("SELECT count(*) FROM services WHERE server_id = :id"),
            {"id": server_id},
        ).scalar()
        assert remaining == 0

    def test_one_application_on_many_servers_and_vice_versa(self, connection: Connection) -> None:
        """PRD §4.9 acceptance criterion."""
        account_id = _account(connection)
        first = _server(connection, account_id, resource_id="i-a", hostname="a.example")
        second = _server(connection, account_id, resource_id="i-b", hostname="b.example")
        app_one, app_two = uuid.uuid4(), uuid.uuid4()
        for app_id, slug in ((app_one, "app-one"), (app_two, "app-two")):
            connection.execute(
                text(
                    """
                    INSERT INTO applications (id, name, slug, health_check_config,
                                              status, created_at, updated_at)
                    VALUES (:id, :slug, :slug, '{}'::jsonb, 'ACTIVE', now(), now())
                    """
                ),
                {"id": app_id, "slug": slug},
            )
        for app_id in (app_one, app_two):
            for server_id in (first, second):
                connection.execute(
                    text(
                        """
                        INSERT INTO application_servers (application_id, server_id,
                                                         created_at)
                        VALUES (:app_id, :server_id, now())
                        """
                    ),
                    {"app_id": app_id, "server_id": server_id},
                )
        count = connection.execute(text("SELECT count(*) FROM application_servers")).scalar()
        assert count == 4


class TestUserConstraints:
    def test_identity_is_keyed_on_issuer_and_subject(self, connection: Connection) -> None:
        payload = {
            "issuer": "https://idp.example.invalid",
            "subject": "placeholder-subject",
        }
        for index in range(2):
            statement = text(
                """
                INSERT INTO users (id, identity_provider_subject,
                                   identity_provider_issuer, email, display_name,
                                   status, created_at, updated_at)
                VALUES (:id, :subject, :issuer, :email, 'Placeholder', 'ACTIVE',
                        now(), now())
                """
            )
            params = {
                "id": uuid.uuid4(),
                "email": f"placeholder{index}@example.invalid",
                **payload,
            }
            if index == 0:
                connection.execute(statement, params)
            else:
                with pytest.raises(IntegrityError):
                    connection.execute(statement, params)

    def test_active_email_uniqueness_is_case_insensitive(self, connection: Connection) -> None:
        statement = text(
            """
            INSERT INTO users (id, identity_provider_subject,
                               identity_provider_issuer, email, display_name,
                               status, created_at, updated_at)
            VALUES (:id, :subject, 'https://idp.example.invalid', :email,
                    'Placeholder', 'ACTIVE', now(), now())
            """
        )
        connection.execute(
            statement,
            {
                "id": uuid.uuid4(),
                "subject": "subject-one",
                "email": "Placeholder@Example.Invalid",
            },
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                statement,
                {
                    "id": uuid.uuid4(),
                    "subject": "subject-two",
                    "email": "placeholder@example.invalid",
                },
            )


class TestUpdatedAtTrigger:
    def test_updated_at_advances_without_application_help(self, migrated: Engine) -> None:
        """Uses separate transactions on purpose.

        ``now()`` returns the *transaction* start time and is constant within one
        transaction, so insert-then-update inside a single transaction yields
        identical timestamps. That is correct behaviour — rows changed together
        share a timestamp — but it means advancement is only observable across
        transactions.
        """
        select = text("SELECT updated_at FROM cloud_accounts WHERE id = :id")
        with migrated.begin() as connection:
            account_id = _account(connection, "000000009999")
            before = connection.execute(select, {"id": account_id}).scalar()
        try:
            with migrated.begin() as connection:
                connection.execute(
                    text("UPDATE cloud_accounts SET display_name = 'Renamed' WHERE id = :id"),
                    {"id": account_id},
                )
            with migrated.connect() as connection:
                after = connection.execute(select, {"id": account_id}).scalar()
            assert before is not None and after is not None
            assert after > before
        finally:
            with migrated.begin() as connection:
                connection.execute(
                    text("DELETE FROM cloud_accounts WHERE id = :id"), {"id": account_id}
                )

    def test_trigger_overrides_a_client_supplied_updated_at(self, connection: Connection) -> None:
        """The database owns the clock, so a wrong client clock cannot lie."""
        account_id = _account(connection)
        connection.execute(
            text(
                """
                UPDATE cloud_accounts
                   SET display_name = 'Renamed',
                       updated_at = '1999-01-01T00:00:00Z'
                 WHERE id = :id
                """
            ),
            {"id": account_id},
        )
        after = connection.execute(
            text("SELECT updated_at FROM cloud_accounts WHERE id = :id"),
            {"id": account_id},
        ).scalar()
        assert after.year > 2000


class TestSeededReferenceData:
    def test_five_system_roles_are_seeded(self, connection: Connection) -> None:
        names = {
            row[0] for row in connection.execute(text("SELECT name FROM roles WHERE is_system"))
        }
        assert names == {
            "VIEWER",
            "DEVELOPER",
            "DEVOPS_ENGINEER",
            "APPROVER",
            "ADMINISTRATOR",
        }

    @pytest.mark.security
    def test_administrator_cannot_approve(self, connection: Connection) -> None:
        """PRD §3.6: separation of duties is meaningless otherwise (OPEN-10)."""
        rows = connection.execute(
            text(
                """
                SELECT p.resource_type, p.action
                  FROM role_permissions rp
                  JOIN roles r ON r.id = rp.role_id
                  JOIN permissions p ON p.id = rp.permission_id
                 WHERE r.name = 'ADMINISTRATOR' AND p.action = 'approve'
                """
            )
        ).fetchall()
        assert rows == []

    @pytest.mark.security
    def test_viewer_has_only_read_permissions(self, connection: Connection) -> None:
        actions = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT DISTINCT p.action
                      FROM role_permissions rp
                      JOIN roles r ON r.id = rp.role_id
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE r.name = 'VIEWER'
                    """
                )
            )
        }
        # `read` and the `read:<scope>` forms (migration 0007: `logs:read:application`).
        # Anything else -- `query`, `write`, `start` -- is not a read.
        assert actions == {"read", "read:application"}
        assert all(action == "read" or action.startswith("read:") for action in actions)

    @pytest.mark.security
    def test_only_approver_holds_approve_permissions(self, connection: Connection) -> None:
        roles = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT DISTINCT r.name
                      FROM role_permissions rp
                      JOIN roles r ON r.id = rp.role_id
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE p.action = 'approve'
                    """
                )
            )
        }
        assert roles == {"APPROVER"}

    def test_on_prem_account_exists_for_non_cloud_servers(self, connection: Connection) -> None:
        """servers.cloud_account_id is NOT NULL, so this row must exist."""
        count = connection.execute(
            text("SELECT count(*) FROM cloud_accounts WHERE provider = 'ON_PREM'")
        ).scalar()
        assert count == 1

    def test_production_approval_policies_default_to_no_self_approval(
        self, connection: Connection
    ) -> None:
        rows = connection.execute(
            text(
                """
                SELECT allow_self_approval, required_approvals
                  FROM approval_policies WHERE environment = 'production'
                """
            )
        ).fetchall()
        assert rows
        assert all(not row[0] and row[1] >= 1 for row in rows)

    @pytest.mark.security
    def test_no_seeded_policy_grants_a_stop_action(self, connection: Connection) -> None:
        """Stopping a service and leaving it down is not a default capability."""
        rows = connection.execute(
            # The *seeded* policies: other suites commit test-only policies (``test-ops-all``)
            # into this shared schema, and which of them have run before this test depends on
            # file order -- M4.5's ``test_approval*`` files sort ahead of this one.
            text(
                "SELECT name, allowed_actions FROM service_action_policies"
                " WHERE name IN ('read-only', 'restart-only')"
            )
        ).fetchall()
        assert rows
        for name, actions in rows:
            assert "stop" not in actions, f"{name} grants stop by default"


def test_statement_timeout_is_applied_to_connections(migrated: object) -> None:
    """A query without a bound is a query that can hang a worker."""
    from app.db.session import get_sync_engine

    engine = get_sync_engine()
    with engine.connect() as connection:
        value = connection.execute(text("SHOW statement_timeout")).scalar()
    assert value not in (None, "0")


def test_audit_insert_outside_every_partition_fails_loudly(
    connection: Connection,
) -> None:
    """No default partition, by design (app/db/partitions.py).

    A catch-all would silently absorb out-of-range rows and defeat
    retention-by-partition-drop. Failing is the intended behaviour, and the
    runway monitor is what stops it happening in practice.
    """
    with pytest.raises((IntegrityError, DBAPIError)):
        connection.execute(
            text(
                """
                INSERT INTO audit_events (id, occurred_at, actor_type, actor_label,
                                          action_type, correlation_id, outcome)
                VALUES (:id, '1999-01-01T00:00:00Z', 'SYSTEM', 'system:test',
                        'TEST', :correlation_id, 'SUCCESS')
                """
            ),
            {"id": uuid.uuid4(), "correlation_id": uuid.uuid4()},
        )
