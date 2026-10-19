"""The notification tables and the outbox write against real PostgreSQL (M3.4, OPEN-50).

The invariants: one delivery per (alert, channel) as a database fact; a terminal row never
looks as if it is waiting for a retry; and a delivery is created **in the alert's own
transaction**, only for a *new* alert row, only for an active channel whose rules match the
stored row.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.alerts import store
from app.alerts.lifecycle import dedup_key

NOW = datetime.now(UTC)


def key() -> str:
    return dedup_key(
        source="PROMETHEUS",
        rule_name="HostCpuWarning",
        target=str(uuid.uuid4()),
        discriminators={},
    )


def a_channel(
    connection: Connection,
    *,
    rules: Any = None,
    status: str = "ACTIVE",
    channel_type: str = "SLACK",
    reference: str = "env://OPS",
) -> uuid.UUID:
    channel = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO notification_channels (id, name, channel_type, destination_reference,"
            " routing_rules, status) VALUES (:id, :n, :t, :r, CAST(:rules AS jsonb), :s)"
        ),
        {
            "id": channel,
            "n": f"channel-{channel}",
            "t": channel_type,
            "r": reference,
            "rules": json.dumps({} if rules is None else rules),
            "s": status,
        },
    )
    return channel


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


def deliveries(connection: Connection, dedup: str | None = None) -> list[Any]:
    query = (
        "SELECT d.channel_id, d.status, d.attempt_count, d.next_attempt_at, d.alert_event_id"
        " FROM notification_deliveries d"
    )
    params: dict[str, Any] = {}
    if dedup is not None:
        query += " JOIN alert_events a ON a.id = d.alert_event_id WHERE a.dedup_key = :k"
        params["k"] = dedup
    return list(connection.execute(text(query), params).all())


def server_target(environment: str = "production") -> store.ResolvedTarget:
    return store.ResolvedTarget("server", uuid.uuid4(), uuid.uuid4(), environment)


# --- constraints ---------------------------------------------------------------------------


def insert_delivery(connection: Connection, channel: uuid.UUID, **overrides: Any) -> None:
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "alert": uuid.uuid4(),
        "channel": channel,
        "status": "PENDING",
        "attempts": 0,
        "next": NOW,
        "sent": None,
    }
    row.update(overrides)
    connection.execute(
        text(
            "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
            " attempt_count, next_attempt_at, sent_at) VALUES (:id, :alert, :channel, :status,"
            " :attempts, :next, :sent)"
        ),
        row,
    )


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"status": "DONE"}, "status is a closed set"),
        ({"attempts": -1}, "attempts cannot be negative"),
        ({"attempts": 21}, "attempts are bounded"),
        ({"status": "SENT", "next": None}, "a sent delivery has a sent_at"),
        ({"status": "PENDING", "sent": NOW}, "a pending delivery has not been sent"),
        ({"status": "FAILED", "next": NOW}, "a failed delivery is not waiting for a retry"),
        ({"status": "ABANDONED", "next": NOW}, "an abandoned delivery is not waiting either"),
        ({"status": "SENT", "next": NOW, "sent": NOW}, "a sent delivery is not waiting"),
        ({"status": "PENDING", "next": None}, "a pending delivery needs a due time"),
    ],
)
def test_the_delivery_table_rejects_what_it_should(
    connection: Connection, changes: dict[str, Any], why: str
) -> None:
    channel = a_channel(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_delivery(connection, channel, **changes)


def test_legal_terminal_rows_are_accepted(connection: Connection) -> None:
    channel = a_channel(connection)
    insert_delivery(connection, channel, status="SENT", next=None, sent=NOW)
    insert_delivery(connection, channel, status="FAILED", next=None)
    insert_delivery(connection, channel, status="ABANDONED", next=None, attempts=5)


def test_one_delivery_per_alert_and_channel(connection: Connection) -> None:
    channel = a_channel(connection)
    alert = uuid.uuid4()
    insert_delivery(connection, channel, alert=alert)
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_delivery(connection, channel, alert=alert)
    insert_delivery(connection, a_channel(connection), alert=alert)  # another channel is fine


def test_a_channel_with_deliveries_cannot_be_deleted(connection: Connection) -> None:
    channel = a_channel(connection)
    insert_delivery(connection, channel)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})


@pytest.mark.parametrize(
    "changes",
    [{"channel_type": "PAGER"}, {"status": "PAUSED"}],
)
def test_the_channel_table_rejects_unknown_types_and_statuses(
    connection: Connection, changes: dict[str, str]
) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        a_channel(connection, **changes)


def test_channel_names_are_unique(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO notification_channels (id, name, channel_type, destination_reference)"
            " VALUES (:i, 'ops', 'EMAIL', 'a@example.com')"
        ),
        {"i": uuid.uuid4()},
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO notification_channels (id, name, channel_type, destination_reference)"
                " VALUES (:i, 'ops', 'EMAIL', 'b@example.com')"
            ),
            {"i": uuid.uuid4()},
        )


def test_the_updated_at_trigger_is_on_both_tables(connection: Connection) -> None:
    triggers = {
        row[0]
        for row in connection.execute(
            text(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_notif%'"
            )
        )
    }
    assert triggers == {
        "trg_notification_channels_updated_at",
        "trg_notification_deliveries_updated_at",
    }


# --- the outbox ----------------------------------------------------------------------------


def test_a_new_alert_creates_one_pending_delivery_per_matching_channel(
    connection: Connection,
) -> None:
    session = Session(bind=connection)
    everyone = a_channel(connection)
    only_critical = a_channel(connection, rules={"severity": ["CRITICAL"]})
    prod = a_channel(connection, rules={"environment": ["production"]})
    staging = a_channel(connection, rules={"environment": ["staging"]})
    k = key()
    assert fire(session, k, target=server_target("production")) == "created"
    got = {row.channel_id: row for row in deliveries(connection, k)}
    assert set(got) == {everyone, prod}
    assert only_critical not in got and staging not in got
    assert all(row.status == "PENDING" and row.attempt_count == 0 for row in got.values())
    assert all(row.next_attempt_at is not None for row in got.values())


def test_a_repeat_a_resolve_and_a_recurrence_notify_correctly(connection: Connection) -> None:
    """Only a new alert ROW notifies (OPEN-51): not a repeat, not a resolve."""
    session = Session(bind=connection)
    channel = a_channel(connection)
    k = key()
    assert fire(session, k) == "created"
    assert fire(session, k) == "updated"
    assert fire(session, k) == "updated"
    assert store.resolve(session, dedup=k, now=datetime.now(UTC)) == "resolved"
    assert len(deliveries(connection, k)) == 1
    # A recurrence after the close is a NEW row, so it is notified again.
    assert fire(session, k) == "created"
    rows = deliveries(connection, k)
    assert len(rows) == 2 and {row.channel_id for row in rows} == {channel}


def test_a_disabled_channel_and_a_channel_with_unreadable_rules_receive_nothing(
    connection: Connection,
) -> None:
    session = Session(bind=connection)
    a_channel(connection, status="DISABLED")
    a_channel(connection, rules={"severity": ["URGENT"]})  # hand-edited into nonsense
    a_channel(connection, rules={"surprise": True})
    a_channel(connection, rules=["not", "an", "object"])
    k = key()
    assert fire(session, k, target=server_target()) == "created"
    assert deliveries(connection, k) == []


def test_an_alert_with_no_environment_reaches_only_unrestricted_channels(
    connection: Connection,
) -> None:
    """OPEN-52/OPEN-45: an unlinked alert cannot place itself in an environment's channel."""
    session = Session(bind=connection)
    catch_all = a_channel(connection)
    restricted = a_channel(connection, rules={"environment": ["production"]})
    k = key()
    assert fire(session, k, target=store.UNRESOLVED) == "created"
    assert {row.channel_id for row in deliveries(connection, k)} == {catch_all}
    assert restricted not in {row.channel_id for row in deliveries(connection, k)}


def test_the_delivery_is_written_in_the_alerts_own_transaction(migrated: Engine) -> None:
    """A rollback takes the alert and its deliveries together; nothing is left over."""
    with migrated.begin() as setup:
        channel = a_channel(setup)
    k = key()
    session = Session(migrated)
    assert fire(session, k) == "created"
    session.rollback()
    session.close()
    with migrated.connect() as check:
        assert deliveries(check, k) == []
    with migrated.begin() as cleanup:
        cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})


def test_concurrent_firings_of_one_key_make_exactly_one_delivery(migrated: Engine) -> None:
    with migrated.begin() as setup:
        channel = a_channel(setup)
    k = key()
    workers = 8
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with Session(migrated) as session:
                barrier.wait(timeout=10)
                fire(session, k)
                session.commit()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    with migrated.connect() as check:
        assert len(deliveries(check, k)) == 1
    with migrated.begin() as cleanup:
        cleanup.execute(
            text(
                "DELETE FROM notification_deliveries WHERE alert_event_id IN"
                " (SELECT id FROM alert_events WHERE dedup_key = :k)"
            ),
            {"k": k},
        )
        cleanup.execute(text("DELETE FROM alert_open_keys WHERE dedup_key = :k"), {"k": k})
        cleanup.execute(text("DELETE FROM alert_events WHERE dedup_key = :k"), {"k": k})
        cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})
