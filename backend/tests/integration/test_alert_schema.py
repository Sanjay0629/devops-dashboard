"""The alert tables and write paths against real PostgreSQL (M3.3, OPEN-41, OPEN-42, OPEN-44).

The invariant under test is the one the plan found documented wrongly: *at most one open
alert per ``dedup_key``*. A partitioned table cannot enforce it with a unique index, so
``alert_open_keys`` does -- and the concurrency test below is the one that fails if that
is ever reduced to a convention.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.alerts import runway, store
from app.alerts.lifecycle import EXPIRE_AFTER_SECONDS, dedup_key
from app.db.partitions import (
    PARTITIONED_TABLES,
    ensure_partitions,
    partition_name,
    partition_runway_months,
)
from tests.integration.test_auth_flow import redis_url  # noqa: F401 - fixture used by name

NOW = datetime.now(UTC)


def key(name: str = "HostCpuWarning", server: str | None = None) -> str:
    return dedup_key(
        source="PROMETHEUS", rule_name=name, target=server or str(uuid.uuid4()), discriminators={}
    )


def insert_event(connection: Connection, **overrides: Any) -> uuid.UUID:
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "first_seen_at": NOW,
        "source": "PROMETHEUS",
        "dedup_key": key(),
        "rule_name": "HostCpuWarning",
        "severity": "WARNING",
        "status": "FIRING",
        "summary": "s",
        "last_seen_at": NOW,
        "resolved_at": None,
        "acknowledged_by": None,
        "target_type": None,
        "target_id": None,
        "environment": None,
        "occurrence_count": 1,
    }
    row.update(overrides)
    connection.execute(
        text(
            "INSERT INTO alert_events (id, first_seen_at, source, dedup_key, rule_name, severity,"
            " status, summary, last_seen_at, resolved_at, acknowledged_by, target_type, target_id,"
            " environment, occurrence_count) VALUES (:id, :first_seen_at, :source, :dedup_key,"
            " :rule_name, :severity, :status, :summary, :last_seen_at, :resolved_at,"
            " :acknowledged_by, :target_type, :target_id, :environment, :occurrence_count)"
        ),
        row,
    )
    return row["id"]


def a_user(connection: Connection) -> uuid.UUID:
    user = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, email, display_name, identity_provider_issuer,"
            " identity_provider_subject, status) VALUES (:i, :e, 'T', 'https://idp.invalid', :s,"
            " 'ACTIVE')"
        ),
        {"i": user, "e": f"{user}@example.invalid", "s": str(user)},
    )
    return user


# --- constraints --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"status": "BOGUS"}, "status is a closed set"),
        ({"severity": "FATAL"}, "severity is a closed set"),
        ({"source": "EMAIL"}, "source is a closed set"),
        ({"environment": "prod"}, "environment is a closed set"),
        ({"target_type": "server"}, "a target type needs a target id"),
        ({"target_id": uuid.uuid4()}, "a target id needs a target type"),
        ({"target_type": "bogus", "target_id": uuid.uuid4()}, "target type is a closed set"),
        ({"occurrence_count": 0}, "an alert has occurred at least once"),
        ({"status": "ACKNOWLEDGED"}, "an acknowledgement needs an acknowledger"),
        (
            {"status": "FIRING", "resolved_at": NOW},
            "a firing alert carries no resolution time",
        ),
        ({"status": "RESOLVED"}, "a resolved alert carries one"),
        ({"status": "EXPIRED", "resolved_at": NOW}, "expiry is not resolution"),
    ],
)
def test_the_table_rejects_what_it_should(
    connection: Connection, changes: dict[str, Any], why: str
) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_event(connection, **changes)


def test_acknowledging_can_never_carry_a_resolution_time(connection: Connection) -> None:
    """PRD §4.8, as a schema fact rather than an application habit."""
    user = a_user(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_event(connection, status="ACKNOWLEDGED", acknowledged_by=user, resolved_at=NOW)
    insert_event(connection, status="ACKNOWLEDGED", acknowledged_by=user)  # and this is fine


def test_a_row_outside_every_partition_fails_loudly(connection: Connection) -> None:
    with pytest.raises(DBAPIError), connection.begin_nested():
        insert_event(connection, first_seen_at=datetime(2100, 1, 1, tzinfo=UTC))


def test_the_open_key_table_enforces_one_open_alert_per_key(connection: Connection) -> None:
    k = key()
    connection.execute(
        text("INSERT INTO alert_open_keys VALUES (:k, :a, now())"), {"k": k, "a": uuid.uuid4()}
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("INSERT INTO alert_open_keys VALUES (:k, :a, now())"),
            {"k": k, "a": uuid.uuid4()},
        )


def test_the_updated_at_trigger_is_on_the_table_and_on_every_partition(
    connection: Connection,
) -> None:
    """``now()`` is the transaction's start, so a rolled-back test cannot watch it tick;
    what can be asserted is that the trigger exists where rows are actually written."""
    rows = connection.execute(
        text(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
            " WHERE t.tgname = 'trg_alert_events_updated_at' AND NOT t.tgisinternal"
        )
    ).scalars()
    names = set(rows)
    assert "alert_events" in names
    assert partition_name(NOW.date(), "alert_events") in names  # cloned to the partition


def test_alert_events_is_partitioned_and_has_a_runway(connection: Connection) -> None:
    kind = connection.execute(
        text("SELECT relkind FROM pg_class WHERE relname = 'alert_events'")
    ).scalar()
    assert kind == "p"
    assert partition_runway_months(connection, parent="alert_events") >= 2
    assert (
        connection.execute(
            text("SELECT to_regclass(:n) IS NOT NULL"),
            {"n": f"public.{partition_name(NOW.date(), 'alert_events')}"},
        ).scalar()
        is True
    )


def test_ensure_partitions_extends_each_table_and_refuses_an_unknown_one(
    connection: Connection,
) -> None:
    far = datetime.now(UTC) + timedelta(days=200)
    for table in PARTITIONED_TABLES:
        created = ensure_partitions(connection, reference=far, parent=table)
        assert created and all(name.startswith(f"{table}_") for name in created)
    with pytest.raises(ValueError):
        ensure_partitions(connection, parent="users; DROP TABLE users")


# --- the write paths -------------------------------------------------------------------


def fire(session: Session, dedup: str, **changes: Any) -> str:
    args: dict[str, Any] = {
        "source": "PROMETHEUS",
        "dedup": dedup,
        "rule_name": "HostCpuWarning",
        "severity": "WARNING",
        "target": store.UNRESOLVED,
        "summary": "s",
        "details": {},
        "now": datetime.now(UTC),
    }
    args.update(changes)
    return store.fire(session, **args)


def counts(session: Session, dedup: str) -> tuple[int, int, int]:
    open_rows = session.execute(
        text(
            "SELECT count(*) FROM alert_events WHERE dedup_key = :k AND status IN"
            " ('FIRING','ACKNOWLEDGED')"
        ),
        {"k": dedup},
    ).scalar_one()
    keys = session.execute(
        text("SELECT count(*) FROM alert_open_keys WHERE dedup_key = :k"), {"k": dedup}
    ).scalar_one()
    total = session.execute(
        text("SELECT count(*) FROM alert_events WHERE dedup_key = :k"), {"k": dedup}
    ).scalar_one()
    return int(open_rows), int(keys), int(total)


def test_fire_then_repeat_then_resolve(connection: Connection) -> None:
    session = Session(bind=connection)
    k = key()
    assert fire(session, k) == "created"
    assert fire(session, k) == "updated"
    assert fire(session, k) == "updated"
    assert counts(session, k) == (1, 1, 1)
    assert (
        session.execute(
            text("SELECT occurrence_count FROM alert_events WHERE dedup_key = :k"), {"k": k}
        ).scalar_one()
        == 3
    )

    assert store.resolve(session, dedup=k, now=datetime.now(UTC)) == "resolved"
    assert counts(session, k) == (0, 0, 1)  # history kept, no open key
    row = session.execute(
        text("SELECT status, resolved_at FROM alert_events WHERE dedup_key = :k"), {"k": k}
    ).one()
    assert row.status == "RESOLVED" and row.resolved_at is not None

    assert store.resolve(session, dedup=k, now=datetime.now(UTC)) == "ignored"  # nothing open
    assert fire(session, k) == "created"  # a recurrence is a new row, not a reopened one
    assert counts(session, k) == (1, 1, 2)


def test_a_resolved_for_something_never_opened_creates_nothing(connection: Connection) -> None:
    session = Session(bind=connection)
    k = key()
    assert store.resolve(session, dedup=k, now=datetime.now(UTC)) == "ignored"
    assert counts(session, k) == (0, 0, 0)


def test_resolving_an_acknowledged_alert_resolves_it(connection: Connection) -> None:
    session = Session(bind=connection)
    user = a_user(connection)
    k = key()
    fire(session, k)
    session.execute(
        text(
            "UPDATE alert_events SET status='ACKNOWLEDGED', acknowledged_by=:u,"
            " acknowledged_at=now() WHERE dedup_key=:k"
        ),
        {"u": user, "k": k},
    )
    assert fire(session, k) == "updated"  # still open while acknowledged
    assert store.resolve(session, dedup=k, now=datetime.now(UTC)) == "resolved"
    status = session.execute(
        text("SELECT status FROM alert_events WHERE dedup_key = :k"), {"k": k}
    ).scalar_one()
    assert status == "RESOLVED"


def test_a_dangling_key_is_repaired_rather_than_swallowing_the_alert(
    connection: Connection,
) -> None:
    """A key pointing at no open event must not make an alert vanish."""
    session = Session(bind=connection)
    k = key()
    connection.execute(
        text("INSERT INTO alert_open_keys VALUES (:k, :a, now())"), {"k": k, "a": uuid.uuid4()}
    )
    assert fire(session, k) == "created"
    assert counts(session, k) == (1, 1, 1)


def test_an_expired_alert_is_not_confirmed_either_way(connection: Connection) -> None:
    session = Session(bind=connection)
    stale, fresh = key(), key()
    fire(session, stale, now=datetime.now(UTC) - timedelta(seconds=EXPIRE_AFTER_SECONDS + 60))
    fire(session, fresh)
    assert store.expire(session, after_seconds=EXPIRE_AFTER_SECONDS) >= 1

    row = session.execute(
        text("SELECT status, resolved_at FROM alert_events WHERE dedup_key = :k"), {"k": stale}
    ).one()
    assert row.status == "EXPIRED" and row.resolved_at is None  # expiry is not resolution
    assert counts(session, stale) == (0, 0, 1)
    assert counts(session, fresh) == (1, 1, 1)  # untouched
    assert fire(session, stale) == "created"  # and a returning problem is a new row


def test_expiry_counts_acknowledged_alerts_too(connection: Connection) -> None:
    session = Session(bind=connection)
    user = a_user(connection)
    k = key()
    fire(session, k, now=datetime.now(UTC) - timedelta(seconds=EXPIRE_AFTER_SECONDS + 60))
    session.execute(
        text(
            "UPDATE alert_events SET status='ACKNOWLEDGED', acknowledged_by=:u WHERE dedup_key=:k"
        ),
        {"u": user, "k": k},
    )
    store.expire(session, after_seconds=EXPIRE_AFTER_SECONDS)
    status = session.execute(
        text("SELECT status FROM alert_events WHERE dedup_key = :k"), {"k": k}
    ).scalar_one()
    assert status == "EXPIRED"


# --- the invariant under concurrency -------------------------------------------------------


def test_concurrent_firing_of_one_key_leaves_exactly_one_open_row(migrated: Engine) -> None:
    """The test that fails if OPEN-42 is ever reduced to a convention."""
    k = key()
    workers = 12
    barrier = threading.Barrier(workers)
    results: list[str] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with Session(migrated) as session:
                barrier.wait(timeout=10)
                results.append(fire(session, k))
                session.commit()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert results.count("created") == 1 and results.count("updated") == workers - 1
    with Session(migrated) as session:
        assert counts(session, k) == (1, 1, 1)
        assert (
            session.execute(
                text("SELECT occurrence_count FROM alert_events WHERE dedup_key = :k"), {"k": k}
            ).scalar_one()
            == workers
        )


def test_a_second_firing_waits_for_the_first_to_commit_and_then_counts_as_a_repeat(
    migrated: Engine,
) -> None:
    """Deterministic version of the race: A holds the key uncommitted, B must wait on it.

    With a check-then-insert B would not see A's uncommitted key, try to insert and fail on
    the primary key once A commits. With the arbiter B blocks, then sees the conflict and
    updates the open row. (The test above is the same invariant under load; this one
    cannot pass by luck of timing.)
    """
    import time

    k = key()
    outcome: dict[str, Any] = {}
    first = Session(migrated)
    assert fire(first, k) == "created"  # the key is inserted and NOT committed

    def second() -> None:
        try:
            with Session(migrated) as session:
                outcome["result"] = fire(session, k)
                session.commit()
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=second)
    thread.start()
    time.sleep(1.0)
    assert thread.is_alive(), "B should be blocked on A's uncommitted key"
    first.commit()
    thread.join(timeout=20)
    first.close()

    assert outcome.get("error") is None, outcome
    assert outcome["result"] == "updated"
    with Session(migrated) as session:
        assert counts(session, k) == (1, 1, 1)


# --- the runway monitor ---------------------------------------------------------------------


def _drop_future_partitions(connection: Connection, table: str, *, keep: int) -> None:
    """Leave ``keep`` months of runway (inside the test's rolled-back transaction)."""
    months = partition_runway_months(connection, parent=table)
    now = datetime.now(UTC).date()
    from app.db.partitions import _month_start, _next_month

    candidate = _next_month(_month_start(now))
    existing = []
    while len(existing) < months:
        existing.append(candidate)
        candidate = _next_month(candidate)
    for month in existing[keep:]:
        connection.execute(text(f"DROP TABLE {partition_name(month, table)}"))


def open_runway_alerts(session: Session, table: str) -> dict[str, str]:
    rows = session.execute(
        text(
            "SELECT rule_name, status FROM alert_events WHERE source = 'DCP_SYSTEM'"
            " AND details->>'table' = :t AND status IN ('FIRING','ACKNOWLEDGED')"
        ),
        {"t": table},
    )
    return {row.rule_name: row.status for row in rows}


def test_a_healthy_runway_raises_nothing(connection: Connection) -> None:
    session = Session(bind=connection)
    runway.sync_alerts(session)
    for table in PARTITIONED_TABLES:
        assert open_runway_alerts(session, table) == {}


def test_a_short_runway_raises_a_warning_and_an_empty_one_a_critical_and_recovery_closes_it(
    connection: Connection,
) -> None:
    session = Session(bind=connection)
    _drop_future_partitions(connection, "alert_events", keep=1)
    runway.sync_alerts(session)
    assert open_runway_alerts(session, "alert_events") == {"PartitionRunwayLow": "FIRING"}
    assert open_runway_alerts(session, "audit_events") == {}  # the other table is fine

    _drop_future_partitions(connection, "alert_events", keep=0)
    runway.sync_alerts(session)
    # Mutually exclusive: the warning closed as the critical opened.
    assert open_runway_alerts(session, "alert_events") == {"PartitionRunwayExhausted": "FIRING"}

    ensure_partitions(connection, parent="alert_events")
    runway.sync_alerts(session)
    assert open_runway_alerts(session, "alert_events") == {}
    resolved = session.execute(
        text(
            "SELECT count(*) FROM alert_events WHERE source = 'DCP_SYSTEM'"
            " AND details->>'table' = 'alert_events' AND status = 'RESOLVED'"
        )
    ).scalar_one()
    assert resolved == 2  # history kept


def test_the_runway_alert_is_about_no_server(connection: Connection) -> None:
    session = Session(bind=connection)
    _drop_future_partitions(connection, "audit_events", keep=0)
    runway.sync_alerts(session)
    row = session.execute(
        text(
            "SELECT target_type, target_id, environment, severity FROM alert_events"
            " WHERE source = 'DCP_SYSTEM' AND details->>'table' = 'audit_events'"
            " AND status = 'FIRING'"
        )
    ).one()
    assert (row.target_type, row.target_id, row.environment) == (None, None, None)
    assert row.severity == "CRITICAL"


# --- the scheduled tasks ----------------------------------------------------------------------


def _import_tasks(broker_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import the task module with the *test* Redis as the Celery broker.

    ``celery_app`` is a module-level singleton configured from the settings of whichever
    test imports it first. Imported under the unit-test placeholder (an unreachable Redis)
    it would make every later server registration spend ~65 s retrying its enqueue -- found
    by the first full run of this suite, not by reading.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("DCP_REDIS_URL", broker_url)
    get_settings.cache_clear()
    from app.workers.tasks import maintenance

    return maintenance


def test_the_expiry_task_expires_a_stale_alert_and_leaves_a_fresh_one(
    migrated: Engine,
    redis_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expire_alerts = _import_tasks(redis_url, monkeypatch).expire_alerts

    stale, fresh = key(), key()
    with Session(migrated) as session:
        fire(session, stale, now=datetime.now(UTC) - timedelta(seconds=EXPIRE_AFTER_SECONDS + 600))
        fire(session, fresh)
        session.commit()

    assert expire_alerts()["expired"] >= 1

    with Session(migrated) as session:
        assert counts(session, stale) == (0, 0, 1)
        assert counts(session, fresh) == (1, 1, 1)


def test_the_partition_task_covers_both_tables_and_leaves_no_open_runway_alert(
    migrated: Engine,
    redis_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_partitions_task = _import_tasks(redis_url, monkeypatch).ensure_partitions_task

    result = ensure_partitions_task()

    assert set(result["runway_months"]) == set(PARTITIONED_TABLES)
    assert all(months >= 2 for months in result["runway_months"].values())
    with Session(migrated) as session:
        open_alerts = session.execute(
            text(
                "SELECT count(*) FROM alert_events WHERE source = 'DCP_SYSTEM'"
                " AND details->>'table' IS NOT NULL AND status IN ('FIRING','ACKNOWLEDGED')"
            )
        ).scalar_one()
    # Only the runway alerts: another test's committed DCP_SYSTEM alert is not this task's.
    assert open_alerts == 0
