"""Migration tests against real PostgreSQL (docs/TESTING.md §4)."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.db.base import Base, all_models
from app.db.partitions import PARTITIONED_TABLES
from tests.integration.conftest import _drop_everything

EXPECTED_TABLES = {
    "alembic_version",
    "alert_events",
    "alert_open_keys",
    "application_servers",
    "applications",
    "approval_policies",
    "audit_events",
    "cloud_accounts",
    "permissions",
    "role_permissions",
    "roles",
    "servers",
    "service_action_policies",
    "services",
    "user_roles",
    "users",
}


def test_upgrade_from_empty_creates_every_table(migrated: Engine) -> None:
    present = set(inspect(migrated).get_table_names())
    assert present >= EXPECTED_TABLES


def test_model_metadata_matches_the_migrated_schema(migrated: Engine) -> None:
    """Every mapped table exists with every mapped column.

    Guards the drift that makes a migration and a model disagree.
    """
    all_models()
    inspector = inspect(migrated)
    for name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(name)}
        expected = {column.name for column in table.columns}
        assert expected <= actual, f"{name} is missing {expected - actual}"


def test_autogenerate_detects_no_drift(migrated: Engine, alembic_config: Config) -> None:
    """The strongest form of the previous test.

    If autogenerate would produce any operation, the models and the migrations
    have diverged.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    all_models()
    with migrated.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                # compare_server_default is OFF deliberately. Alembic compares a
                # model's TextClause default against the reflected string form,
                # and for function defaults like now() those never match, so
                # every timestamp column reports permanent false drift. Column,
                # table, index and constraint drift — the parts that actually
                # catch mistakes — are still detected.
                "compare_server_default": False,
                # Partitions are maintenance-managed, not migration-managed.
                "include_object": lambda obj, name, type_, reflected, compare_to: (
                    not (
                        type_ == "table"
                        and name is not None
                        and name.startswith(tuple(f"{t}_" for t in PARTITIONED_TABLES))
                    )
                ),
            },
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"schema drift detected: {diff}"


def test_downgrade_and_upgrade_round_trip(migrated: Engine, alembic_config: Config) -> None:
    """One revision down and back up, which is what a rollback actually does.

    The two revision numbers are read from the script directory rather than
    written here, so adding a revision does not silently leave this test
    asserting a rollback of the wrong one.
    """
    script = ScriptDirectory.from_config(alembic_config)
    head = script.get_current_head()
    assert head is not None
    previous = script.get_revision(head).down_revision
    assert isinstance(previous, str)

    command.downgrade(alembic_config, "-1")
    with migrated.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert revision == previous

    command.upgrade(alembic_config, "head")
    with migrated.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert revision == head


@pytest.fixture
def rebuilt(migrated: Engine, alembic_config: Config) -> Engine:
    """A schema at head holding only what the migrations themselves put there.

    The three tests below exercise a *downgrade*, and a downgrade unwinds data
    as well as DDL: revision 0002 deletes the reference rows it seeded, which
    it cannot do while another test's server references the seeded cloud
    account (``ON DELETE RESTRICT``, deliberately — docs/DATABASE.md §4.2).
    That is correct behaviour for the migration and the wrong precondition for
    the test, so the schema is rebuilt first rather than the restriction being
    loosened.

    Before M1.5 nothing created inventory rows, so these tests passed on a
    shared schema by luck rather than by design.
    """
    _drop_everything(migrated)
    command.upgrade(alembic_config, "head")
    return migrated


def test_full_downgrade_to_base_leaves_no_tables(rebuilt: Engine, alembic_config: Config) -> None:
    migrated = rebuilt
    command.downgrade(alembic_config, "base")
    remaining = set(inspect(migrated).get_table_names()) - {"alembic_version"}
    assert remaining == set(), f"downgrade left {remaining} behind"
    command.upgrade(alembic_config, "head")


def test_the_migrations_seed_no_user(rebuilt: Engine, alembic_config: Config) -> None:
    """No bootstrap administrator and no default credential ship.

    Asserted here rather than in ``test_constraints.py`` because it is a
    statement about the *migrations*, not about the table's current contents:
    the session-scoped schema is shared, and M1.3's authentication tests
    legitimately create users in it. Users attributed by an audit event cannot
    be deleted afterwards (``actor_id`` is ON DELETE RESTRICT and
    ``audit_events`` is append-only), so a bare `count(*) = 0` would fail for
    the wrong reason. Rebuilding the schema here makes the claim exact.
    """
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    with rebuilt.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 0
        # The reference data the application does need is there, so this is not
        # passing merely because the seed did nothing.
        assert connection.execute(text("SELECT count(*) FROM roles")).scalar() == 5


def test_seed_revision_is_idempotent(rebuilt: Engine, alembic_config: Config) -> None:
    """Re-applying the seed must not duplicate reference data."""
    with rebuilt.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM permissions")).scalar()

    command.downgrade(alembic_config, "0001")
    command.upgrade(alembic_config, "head")

    with rebuilt.connect() as connection:
        after = connection.execute(text("SELECT count(*) FROM permissions")).scalar()
    assert before == after


def test_updated_at_triggers_exist_for_every_timestamped_table(
    migrated: Engine,
) -> None:
    with migrated.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT tgname FROM pg_trigger
                     WHERE NOT tgisinternal AND tgname LIKE 'trg_%_updated_at'
                    """
                )
            )
        }
    expected = {
        f"trg_{table}_updated_at"
        for table in (
            "users",
            "roles",
            "user_roles",
            "cloud_accounts",
            "servers",
            "applications",
            "services",
            "service_action_policies",
            "approval_policies",
        )
    }
    assert expected <= triggers


def test_audit_events_has_no_updated_at_trigger(migrated: Engine) -> None:
    """It has no updated_at column, so a trigger would be meaningless."""
    with migrated.connect() as connection:
        count = connection.execute(
            text(
                """
                SELECT count(*) FROM pg_trigger
                 WHERE tgname = 'trg_audit_events_updated_at'
                """
            )
        ).scalar()
    assert count == 0


def _log_grants(engine: Engine) -> dict[str, set[str]]:
    """Role names holding each log permission, keyed by the permission's action."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT p.action, r.name
                  FROM role_permissions rp
                  JOIN permissions p ON p.id = rp.permission_id
                  JOIN roles r ON r.id = rp.role_id
                 WHERE p.resource_type = 'logs'
                """
            )
        ).all()
    grants: dict[str, set[str]] = {}
    for action, role in rows:
        grants.setdefault(action, set()).add(role)
    return grants


def test_log_permissions_take_the_contract_names_and_no_role_gains_or_loses_one(
    rebuilt: Engine, alembic_config: Config
) -> None:
    """Migration 0007 (OPEN-32) renames in place, so grants follow the permission id.

    The expected sets are docs/SECURITY.md §4's matrix, written out rather than
    read from the migration: system logs are DevOps and above, application logs
    everyone.
    """
    everyone = {"VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER", "ADMINISTRATOR"}
    system = {"DEVOPS_ENGINEER", "APPROVER", "ADMINISTRATOR"}
    assert _log_grants(rebuilt) == {"read:application": everyone, "read:system": system}

    command.downgrade(alembic_config, "0006")
    assert _log_grants(rebuilt) == {"read": everyone, "query": system}
    command.upgrade(alembic_config, "head")
    assert _log_grants(rebuilt) == {"read:application": everyone, "read:system": system}
