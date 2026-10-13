"""Audit partitioning and the runway that keeps it working.

A missing partition is not a cosmetic problem: an ``audit_events`` INSERT
outside every range fails, and because audit writes share the transaction of the
privileged change they record, that failure fails the operation too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, Engine, text

from app.db.partitions import (
    DEFAULT_RUNWAY_MONTHS,
    ensure_partitions,
    partition_name,
    partition_runway_months,
)


def _partitions(connection: Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            text(
                """
                SELECT c.relname FROM pg_class c
                  JOIN pg_inherits i ON i.inhrelid = c.oid
                  JOIN pg_class p ON p.oid = i.inhparent
                 WHERE p.relname = 'audit_events'
                """
            )
        )
    }


def test_migration_creates_the_current_month_and_the_runway(
    connection: Connection,
) -> None:
    existing = _partitions(connection)
    today = datetime.now(UTC).date()
    assert partition_name(today) in existing
    # The migration provisions the current month plus DEFAULT_RUNWAY_MONTHS.
    assert len(existing) >= DEFAULT_RUNWAY_MONTHS + 1


def test_audit_events_is_actually_partitioned(connection: Connection) -> None:
    kind = connection.execute(
        text("SELECT relkind FROM pg_class WHERE relname = 'audit_events'")
    ).scalar()
    # 'p' is a partitioned table; 'r' would mean the partitioning was lost.
    assert kind == "p"


def test_rows_route_to_the_partition_for_their_month(
    connection: Connection,
) -> None:
    event_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO audit_events (id, occurred_at, actor_type, actor_label,
                                      action_type, correlation_id, outcome)
            VALUES (:id, now(), 'SYSTEM', 'system:test', 'TEST', :cid, 'SUCCESS')
            """
        ),
        {"id": event_id, "cid": uuid.uuid4()},
    )
    located = connection.execute(
        text("SELECT tableoid::regclass::text FROM audit_events WHERE id = :id"),
        {"id": event_id},
    ).scalar()
    assert located == partition_name(datetime.now(UTC).date())


def test_a_future_month_already_accepts_writes(connection: Connection) -> None:
    """What the runway is for: next month's first write must not fail."""
    next_month = datetime.now(UTC) + timedelta(days=32)
    connection.execute(
        text(
            """
            INSERT INTO audit_events (id, occurred_at, actor_type, actor_label,
                                      action_type, correlation_id, outcome)
            VALUES (:id, :occurred_at, 'SYSTEM', 'system:test', 'TEST', :cid,
                    'SUCCESS')
            """
        ),
        {"id": uuid.uuid4(), "occurred_at": next_month, "cid": uuid.uuid4()},
    )


def test_ensure_partitions_is_idempotent(migrated: Engine) -> None:
    with migrated.begin() as connection:
        before = _partitions(connection)
        created = ensure_partitions(connection)
        after = _partitions(connection)
    assert created == []
    assert before == after


def test_ensure_partitions_extends_the_runway(migrated: Engine) -> None:
    """Simulate the clock moving forward; the job must fill the gap."""
    future = datetime.now(UTC) + timedelta(days=200)
    with migrated.begin() as connection:
        created = ensure_partitions(connection, reference=future)
        assert created, "no partitions created for a date beyond the runway"
        assert partition_name(future.date()) in _partitions(connection)
        # Clean up so the session-scoped schema stays as the migration left it.
        for name in created:
            connection.execute(text(f"DROP TABLE {name}"))


def test_runway_is_reported_accurately(migrated: Engine) -> None:
    with migrated.connect() as connection:
        runway = partition_runway_months(connection)
    assert runway == DEFAULT_RUNWAY_MONTHS


def test_runway_reaches_zero_when_future_partitions_are_dropped(
    migrated: Engine,
) -> None:
    """The condition the monitor exists to catch."""
    future_names = []
    with migrated.begin() as connection:
        today = datetime.now(UTC).date()
        for row in sorted(_partitions(connection)):
            if row > partition_name(today):
                future_names.append(row)
                connection.execute(text(f"DROP TABLE {row}"))
        assert partition_runway_months(connection) == 0

    # Restore, so later tests see the migrated state.
    with migrated.begin() as connection:
        ensure_partitions(connection)
        assert partition_runway_months(connection) == DEFAULT_RUNWAY_MONTHS


@pytest.mark.security
def test_partitions_inherit_the_immutability_triggers(
    connection: Connection,
) -> None:
    """A trigger on the parent must cover every partition, including new ones."""
    partitions = sorted(_partitions(connection))
    assert partitions
    for name in partitions:
        count = connection.execute(
            text(
                """
                SELECT count(*) FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                 WHERE c.relname = :name AND NOT t.tgisinternal
                """
            ),
            {"name": name},
        ).scalar()
        assert count and count > 0, f"{name} has no immutability trigger"
