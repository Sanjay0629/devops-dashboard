"""The delivery state machine against real PostgreSQL (M3.4, OPEN-50, OPEN-53).

The sender is injected, so these tests are about what DCP *decides* -- lease, retry, abandon,
re-check at send time, closed reason codes -- not about any network. The network paths are the
unit tests for the senders and smoke step 18h.
"""

from __future__ import annotations

import inspect
import json
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from app.alerts import store
from app.alerts.lifecycle import dedup_key
from app.core.config import get_settings
from app.notifications import deliver, render
from app.notifications.outcome import LEASE_SECONDS, Outcome

CANARY = "https://hooks.example.test/services/T0/B0/canary-secret-value"


@pytest.fixture
def session(connection: Connection) -> Iterator[Session]:
    # A savepoint per commit: process() commits and rolls back, and neither may reach the
    # fixture's outer transaction (which is always rolled back at the end of the test).
    with Session(bind=connection, join_transaction_mode="create_savepoint") as s:
        yield s


def delays(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DCP_NOTIFICATION_RETRY_DELAYS_SECONDS", value)
    get_settings.cache_clear()


def a_delivery(
    session: Session,
    connection: Connection,
    *,
    channel_type: str = "SLACK",
    reference: str = "env://OPS",
    channel_status: str = "ACTIVE",
    rules: Any = None,
) -> uuid.UUID:
    channel = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO notification_channels (id, name, channel_type, destination_reference,"
            " routing_rules, status) VALUES (:id, :n, :t, :r, CAST(:rules AS jsonb), :s)"
        ),
        {
            "id": channel,
            "n": f"c-{channel}",
            "t": channel_type,
            "r": reference,
            "rules": json.dumps({} if rules is None else rules),
            "s": channel_status,
        },
    )
    dedup = dedup_key(
        source="PROMETHEUS",
        rule_name="HostCpuCritical",
        target=str(uuid.uuid4()),
        discriminators={},
    )
    store.fire(
        session,
        source="PROMETHEUS",
        dedup=dedup,
        rule_name="HostCpuCritical",
        severity="CRITICAL",
        target=store.ResolvedTarget("server", uuid.uuid4(), uuid.uuid4(), "production"),
        summary="CPU is high",
        details={},
        now=datetime.now(UTC),
    )
    session.commit()
    # fire() created the delivery only if the channel matched; a disabled channel has none,
    # so the row is made by hand for that case.
    row = connection.execute(
        text("SELECT id FROM notification_deliveries WHERE channel_id = :c"), {"c": channel}
    ).scalar_one_or_none()
    if row is None:
        alert = connection.execute(
            text("SELECT id FROM alert_events WHERE dedup_key = :k"), {"k": dedup}
        ).scalar_one()
        row = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
                " next_attempt_at) VALUES (:i, :a, :c, 'PENDING', now())"
            ),
            {"i": row, "a": alert, "c": channel},
        )
    return row  # type: ignore[no-any-return]


def row_of(connection: Connection, delivery: uuid.UUID) -> Any:
    return connection.execute(
        text(
            "SELECT status, attempt_count, last_error, next_attempt_at, sent_at"
            " FROM notification_deliveries WHERE id = :i"
        ),
        {"i": delivery},
    ).one()


class Recorder:
    """A sender that returns scripted outcomes and remembers what it was asked to send."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, render.AlertMessage]] = []

    def __call__(
        self, channel_type: str, destination: str, message: render.AlertMessage
    ) -> Outcome:
        self.calls.append((channel_type, destination, message))
        return self.outcomes.pop(0) if self.outcomes else Outcome("sent")


SENT = Outcome("sent")


def claim(session: Session, **kwargs: Any) -> list[uuid.UUID]:
    """What the dispatcher does: lease, then commit (process() ends its own transaction)."""
    claimed = deliver.claim_due(session, **kwargs)
    session.commit()
    return claimed


def make_due(connection: Connection, delivery: uuid.UUID) -> None:
    connection.execute(
        text(
            "UPDATE notification_deliveries SET next_attempt_at = now() - interval '1 second'"
            " WHERE id = :i AND status = 'PENDING'"
        ),
        {"i": delivery},
    )


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCPSECRET_OPS", CANARY)


# --- claim ---------------------------------------------------------------------------------


def test_claiming_leases_a_due_row_and_counts_the_attempt(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection)
    claimed = claim(session)
    assert delivery in claimed
    row = row_of(connection, delivery)
    assert row.status == "PENDING" and row.attempt_count == 1
    leased_for = (row.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert LEASE_SECONDS - 30 < leased_for <= LEASE_SECONDS
    # The lease makes it invisible: a second claim does not take it again.
    assert delivery not in claim(session)


def test_only_due_pending_rows_are_claimed(session: Session, connection: Connection) -> None:
    future = a_delivery(session, connection)
    connection.execute(
        text(
            "UPDATE notification_deliveries SET next_attempt_at = now() + interval '1 hour'"
            " WHERE id = :i"
        ),
        {"i": future},
    )
    done = a_delivery(session, connection)
    connection.execute(
        text(
            "UPDATE notification_deliveries SET status = 'SENT', next_attempt_at = NULL,"
            " sent_at = now() WHERE id = :i"
        ),
        {"i": done},
    )
    failed = a_delivery(session, connection)
    connection.execute(
        text(
            "UPDATE notification_deliveries SET status = 'FAILED', next_attempt_at = NULL"
            " WHERE id = :i"
        ),
        {"i": failed},
    )
    claimed = set(claim(session))
    assert not claimed & {future, done, failed}


def test_a_batch_is_bounded(session: Session, connection: Connection) -> None:
    for _ in range(5):
        a_delivery(session, connection)
    assert len(claim(session, limit=3)) == 3


def test_two_dispatchers_never_take_the_same_row(migrated: Engine) -> None:
    """FOR UPDATE SKIP LOCKED: concurrent claims are disjoint."""
    ids: list[uuid.UUID] = []
    with migrated.begin() as setup:
        channel = uuid.uuid4()
        setup.execute(
            text(
                "INSERT INTO notification_channels (id, name, channel_type,"
                " destination_reference) VALUES (:c, :n, 'SLACK', 'env://OPS')"
            ),
            {"c": channel, "n": f"claim-{channel}"},
        )
        for _ in range(20):
            ids.append(uuid.uuid4())
            setup.execute(
                text(
                    "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
                    " next_attempt_at) VALUES (:i, :a, :c, 'PENDING', now() - interval '1 minute')"
                ),
                {"i": ids[-1], "a": uuid.uuid4(), "c": channel},
            )
    claims: list[list[uuid.UUID]] = []
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with Session(migrated) as s:
                barrier.wait(timeout=10)
                got = deliver.claim_due(s, limit=20)
                s.commit()
                claims.append(got)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    try:
        assert not errors, errors
        taken = [i for claim in claims for i in claim if i in ids]
        assert len(taken) == len(set(taken)) == 20
    finally:
        with migrated.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM notification_deliveries WHERE channel_id = :c"), {"c": channel}
            )
            cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})


def test_a_claim_in_flight_is_skipped_not_waited_for(migrated: Engine) -> None:
    """Deterministic form of the race above: B must pass over A's locked rows, not block on them."""
    channel = uuid.uuid4()
    with migrated.begin() as setup:
        setup.execute(
            text(
                "INSERT INTO notification_channels (id, name, channel_type,"
                " destination_reference) VALUES (:c, :n, 'SLACK', 'env://OPS')"
            ),
            {"c": channel, "n": f"skip-{channel}"},
        )
        for _ in range(3):
            setup.execute(
                text(
                    "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
                    " next_attempt_at) VALUES (:i, :a, :c, 'PENDING', now() - interval '1 minute')"
                ),
                {"i": uuid.uuid4(), "a": uuid.uuid4(), "c": channel},
            )
    first, second = Session(migrated), Session(migrated)
    try:
        taken_by_first = deliver.claim_due(first, limit=2)  # uncommitted: the rows stay locked
        second.execute(text("SET LOCAL lock_timeout = '1s'"))
        taken_by_second = deliver.claim_due(second, limit=10)
        assert len(taken_by_first) == 2
        assert set(taken_by_first).isdisjoint(taken_by_second)
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        with migrated.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM notification_deliveries WHERE channel_id = :c"), {"c": channel}
            )
            cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})


# --- processing ----------------------------------------------------------------------------


def test_success_records_sent_and_hands_the_sender_the_resolved_destination(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "SENT"
    row = row_of(connection, delivery)
    assert row.status == "SENT" and row.sent_at is not None
    assert row.next_attempt_at is None and row.last_error is None and row.attempt_count == 1
    ((channel_type, destination, message),) = sender.calls
    assert channel_type == "SLACK" and destination == CANARY
    assert message.severity == "CRITICAL" and message.summary == "CPU is high"
    assert message.environment == "production"


def test_email_uses_the_registered_address_not_a_secret(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection, channel_type="EMAIL", reference="ops@example.com")
    claim(session)
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "SENT"
    assert sender.calls[0][:2] == ("EMAIL", "ops@example.com")


def test_a_transient_failure_is_rescheduled_by_the_configured_delay_then_succeeds(
    session: Session, connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays(monkeypatch, "60,300")
    delivery = a_delivery(session, connection)
    sender = Recorder(Outcome("transient", "http_503"), SENT)

    claim(session)
    assert deliver.process(session, delivery, sender=sender) == "PENDING"
    row = row_of(connection, delivery)
    assert row.status == "PENDING" and row.attempt_count == 1 and row.last_error == "http_503"
    wait = (row.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert 30 < wait <= 60

    make_due(connection, delivery)
    assert delivery in claim(session)
    assert deliver.process(session, delivery, sender=sender) == "SENT"
    row = row_of(connection, delivery)
    assert row.status == "SENT" and row.attempt_count == 2
    assert row.last_error is None and row.next_attempt_at is None


def test_transient_failures_past_the_budget_are_abandoned_with_a_reason(
    session: Session, connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays(monkeypatch, "1,1")  # three attempts in all
    delivery = a_delivery(session, connection)
    sender = Recorder(*[Outcome("transient", "timeout")] * 5)
    statuses = []
    for _ in range(3):
        make_due(connection, delivery)
        claim(session)
        statuses.append(deliver.process(session, delivery, sender=sender))
    assert statuses == ["PENDING", "PENDING", "ABANDONED"]
    row = row_of(connection, delivery)
    assert row.status == "ABANDONED" and row.attempt_count == 3 and row.last_error == "timeout"
    assert row.next_attempt_at is None
    assert len(sender.calls) == 3
    assert delivery not in claim(session)  # nothing is retried automatically


def test_a_permanent_failure_stops_at_once(session: Session, connection: Connection) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    sender = Recorder(Outcome("permanent", "http_404"))
    assert deliver.process(session, delivery, sender=sender) == "FAILED"
    row = row_of(connection, delivery)
    assert row.status == "FAILED" and row.attempt_count == 1 and row.last_error == "http_404"
    assert row.next_attempt_at is None


def test_retry_after_lengthens_the_wait(
    session: Session, connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays(monkeypatch, "10,10")
    delivery = a_delivery(session, connection)
    claim(session)
    deliver.process(session, delivery, sender=Recorder(Outcome("transient", "http_429", 600.0)))
    wait = (row_of(connection, delivery).next_attempt_at - datetime.now(UTC)).total_seconds()
    assert 570 < wait <= 600


def test_a_channel_disabled_while_the_task_waited_is_honoured(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    connection.execute(text("UPDATE notification_channels SET status = 'DISABLED'"))
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "FAILED"
    assert row_of(connection, delivery).last_error == "channel_disabled"
    assert not sender.calls  # nothing went out


def test_a_secret_that_has_gone_missing_fails_closed(
    session: Session, connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    monkeypatch.delenv("DCPSECRET_OPS")
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "FAILED"
    assert row_of(connection, delivery).last_error == "secret_unresolvable"
    assert not sender.calls


def test_a_channel_whose_rules_no_longer_validate_sends_nothing(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    connection.execute(
        text('UPDATE notification_channels SET routing_rules = \'{"severity": ["URGENT"]}\'')
    )
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "FAILED"
    assert row_of(connection, delivery).last_error == "channel_rules_invalid"
    assert not sender.calls


def test_a_delivery_for_an_alert_that_no_longer_exists_fails_visibly(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    connection.execute(text("DELETE FROM alert_open_keys"))
    connection.execute(text("DELETE FROM alert_events"))
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "FAILED"
    assert row_of(connection, delivery).last_error == "alert_missing"
    assert not sender.calls


@pytest.mark.parametrize("status", ["SENT", "FAILED", "ABANDONED"])
def test_a_second_delivery_of_a_finished_task_sends_nothing(
    session: Session, connection: Connection, status: str
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    connection.execute(
        text(
            "UPDATE notification_deliveries SET status = :s, next_attempt_at = NULL,"
            " sent_at = CASE WHEN CAST(:k AS text) = 'SENT' THEN now() END WHERE id = :i"
        ),
        {"s": status, "k": status, "i": delivery},
    )
    sender = Recorder(SENT)
    assert deliver.process(session, delivery, sender=sender) == "skipped"
    assert not sender.calls
    assert row_of(connection, delivery).status == status


def test_an_unknown_delivery_is_skipped(session: Session) -> None:
    assert deliver.process(session, uuid.uuid4(), sender=Recorder(SENT)) == "skipped"


def test_recording_never_overwrites_a_finished_row(
    session: Session, connection: Connection
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    deliver.process(session, delivery, sender=Recorder(SENT))
    assert (
        deliver.record(session, delivery, attempts_made=1, outcome=Outcome("permanent", "x"))
        is None
    )
    assert row_of(connection, delivery).status == "SENT"


# --- what never leaves ---------------------------------------------------------------------


def test_the_destination_secret_is_nowhere_it_should_not_be(
    session: Session, connection: Connection, capfd: pytest.CaptureFixture[str]
) -> None:
    delivery = a_delivery(session, connection)
    claim(session)
    deliver.process(session, delivery, sender=Recorder(Outcome("transient", "http_503")))
    captured = capfd.readouterr()
    assert "canary-secret-value" not in captured.out + captured.err
    stored = connection.execute(
        text("SELECT row_to_json(d)::text FROM notification_deliveries d WHERE id = :i"),
        {"i": delivery},
    ).scalar_one()
    assert "canary" not in stored and "hooks.example" not in stored
    channel_row = connection.execute(
        text("SELECT row_to_json(c)::text FROM notification_channels c")
    ).scalar_one()
    assert "canary" not in channel_row  # the channel holds the reference only


def test_task_signatures_take_an_id_and_nothing_else() -> None:
    """SECURITY.md section 22: no URL, credential, command or domain object crosses the broker."""
    from app.workers.tasks import notifications

    assert list(inspect.signature(notifications.deliver_task).parameters) == ["delivery_id"]
    assert list(inspect.signature(notifications.dispatch).parameters) == []


def test_dispatch_enqueues_ids_only(migrated: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.tasks import notifications

    channel, delivery = uuid.uuid4(), uuid.uuid4()
    with migrated.begin() as setup:
        setup.execute(
            text(
                "INSERT INTO notification_channels (id, name, channel_type,"
                " destination_reference) VALUES (:c, :n, 'SLACK', 'env://OPS')"
            ),
            {"c": channel, "n": f"d-{channel}"},
        )
        setup.execute(
            text(
                "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
                " next_attempt_at) VALUES (:i, :a, :c, 'PENDING', now() - interval '1 minute')"
            ),
            {"i": delivery, "a": uuid.uuid4(), "c": channel},
        )
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        notifications.deliver_task,
        "apply_async",
        lambda *args, **kwargs: sent.append({"args": kwargs.get("args"), **kwargs}),
    )
    try:
        result = notifications.dispatch()
        assert result["leased"] >= 1
        mine = [call for call in sent if call["args"] == [str(delivery)]]
        assert len(mine) == 1 and mine[0]["queue"] == "notifications"
        assert all(isinstance(a, str) for call in sent for a in call["args"])
    finally:
        with migrated.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM notification_deliveries WHERE channel_id = :c"), {"c": channel}
            )
            cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})


def test_a_failed_enqueue_leaves_the_row_to_be_picked_up_again(
    migrated: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers.tasks import notifications

    channel, delivery = uuid.uuid4(), uuid.uuid4()
    with migrated.begin() as setup:
        setup.execute(
            text(
                "INSERT INTO notification_channels (id, name, channel_type,"
                " destination_reference) VALUES (:c, :n, 'SLACK', 'env://OPS')"
            ),
            {"c": channel, "n": f"e-{channel}"},
        )
        setup.execute(
            text(
                "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
                " next_attempt_at) VALUES (:i, :a, :c, 'PENDING', now() - interval '1 minute')"
            ),
            {"i": delivery, "a": uuid.uuid4(), "c": channel},
        )

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(notifications.deliver_task, "apply_async", broken)
    try:
        notifications.dispatch()
        with migrated.connect() as check:
            row = row_of(check, delivery)
        # Leased, so not hammered every tick; due again when the lease runs out.
        assert row.status == "PENDING" and row.attempt_count == 1
        assert row.next_attempt_at > datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS - 30)
    finally:
        with migrated.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM notification_deliveries WHERE channel_id = :c"), {"c": channel}
            )
            cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})


# --- retention -----------------------------------------------------------------------------


def test_the_purge_keeps_recent_rows_and_anything_still_waiting(migrated: Engine) -> None:
    from app.workers.tasks import maintenance

    channel = uuid.uuid4()
    rows: dict[str, uuid.UUID] = {}
    with migrated.begin() as setup:
        setup.execute(
            text(
                "INSERT INTO notification_channels (id, name, channel_type,"
                " destination_reference) VALUES (:c, :n, 'SLACK', 'env://OPS')"
            ),
            {"c": channel, "n": f"p-{channel}"},
        )
        for name, status, age in (
            ("old_sent", "SENT", 120),
            ("old_failed", "FAILED", 120),
            ("old_pending", "PENDING", 120),
            ("new_sent", "SENT", 1),
        ):
            rows[name] = uuid.uuid4()
            setup.execute(
                text(
                    "INSERT INTO notification_deliveries (id, alert_event_id, channel_id, status,"
                    " sent_at, next_attempt_at, created_at) VALUES (:i, :a, :c, :s,"
                    " CASE WHEN CAST(:k AS text) = 'SENT' THEN now() END,"
                    " CASE WHEN CAST(:k AS text) = 'PENDING' THEN now() END,"
                    " now() - make_interval(days => :age))"
                ),
                {
                    "i": rows[name],
                    "a": uuid.uuid4(),
                    "c": channel,
                    "s": status,
                    "k": status,
                    "age": age,
                },
            )
    try:
        maintenance.purge_notification_deliveries()
        with migrated.connect() as check:
            left = {
                r[0]
                for r in check.execute(
                    text("SELECT id FROM notification_deliveries WHERE channel_id = :c"),
                    {"c": channel},
                )
            }
        assert left == {rows["old_pending"], rows["new_sent"]}
    finally:
        with migrated.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM notification_deliveries WHERE channel_id = :c"), {"c": channel}
            )
            cleanup.execute(text("DELETE FROM notification_channels WHERE id = :c"), {"c": channel})
