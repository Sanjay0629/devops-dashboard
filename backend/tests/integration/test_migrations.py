"""Migration tests against real PostgreSQL (docs/TESTING.md §4)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.db.base import Base, all_models

EXPECTED_TABLES = {
    "alembic_version",
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
                    not (type_ == "table" and name is not None and name.startswith("audit_events_"))
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


def test_full_downgrade_to_base_leaves_no_tables(migrated: Engine, alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    remaining = set(inspect(migrated).get_table_names()) - {"alembic_version"}
    assert remaining == set(), f"downgrade left {remaining} behind"
    command.upgrade(alembic_config, "head")


def test_the_migrations_seed_no_user(migrated: Engine, alembic_config: Config) -> None:
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
    with migrated.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 0
        # The reference data the application does need is there, so this is not
        # passing merely because the seed did nothing.
        assert connection.execute(text("SELECT count(*) FROM roles")).scalar() == 5


def test_seed_revision_is_idempotent(migrated: Engine, alembic_config: Config) -> None:
    """Re-applying the seed must not duplicate reference data."""
    with migrated.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM permissions")).scalar()

    command.downgrade(alembic_config, "0001")
    command.upgrade(alembic_config, "head")

    with migrated.connect() as connection:
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
