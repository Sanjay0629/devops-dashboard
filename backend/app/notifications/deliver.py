"""The delivery state machine (``OPEN-53``): claim, send, record -- never both at once.

Three steps, each its own short transaction, and **no transaction is open during the send**
(DATABASE.md §12):

1. :func:`claim_due` -- the *dispatcher's* step. One statement leases a batch of due rows
   (``FOR UPDATE SKIP LOCKED``, so two dispatchers never take the same row): the attempt is
   counted and ``next_attempt_at`` is pushed ``LEASE_SECONDS`` ahead. A row whose worker
   dies, or whose task is lost from the queue, is therefore simply due again later --
   **at-least-once**: a duplicate message is possible, a silently lost one is not.
2. :func:`process` -- the *worker's* step. Re-reads everything it acts on (the row, the
   channel, the alert), re-validates the destination, resolves the secret, sends.
3. The outcome is recorded with a guarded update (``WHERE status = 'PENDING'``), so a row
   that something else already finished is not overwritten.

The worker takes a delivery **id**, never a URL, a credential or a message (SECURITY.md §22):
the destination is rebuilt from the registered channel every time, so a channel that was
disabled, edited or whose secret was removed while the task waited is honoured.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import secrets
from app.core.config import get_settings
from app.core.logging import get_logger
from app.notifications import render, senders
from app.notifications.outcome import LEASE_SECONDS, Outcome, classify_reason, next_delay
from app.notifications.rules import parse_stored_rules

logger = get_logger(__name__)

#: Rows leased per dispatcher tick.
BATCH_SIZE: Final = 50

#: ``(channel_type, destination, message) -> Outcome``. Injectable so the state machine is
#: tested without a network; the default is the real pair of senders.
Sender = Callable[[str, str, render.AlertMessage], Outcome]


def default_sender(channel_type: str, destination: str, message: render.AlertMessage) -> Outcome:
    if channel_type == "EMAIL":
        return senders.send_email(destination, message)
    return senders.send_http(channel_type, destination, message)


def claim_due(
    session: Session, *, limit: int = BATCH_SIZE, now: datetime | None = None
) -> list[uuid.UUID]:
    """Lease up to ``limit`` due deliveries and return their ids. Commits nothing."""
    moment = now or datetime.now(UTC)
    rows = session.execute(
        text(
            """
            WITH due AS (
                SELECT id FROM notification_deliveries
                 WHERE status = 'PENDING' AND next_attempt_at <= :now
                 ORDER BY next_attempt_at, id
                 LIMIT :limit
                   FOR UPDATE SKIP LOCKED
            )
            UPDATE notification_deliveries d
               SET attempt_count = d.attempt_count + 1,
                   next_attempt_at = :lease_until
              FROM due
             WHERE d.id = due.id
         RETURNING d.id
            """
        ),
        {"now": moment, "limit": limit, "lease_until": moment + timedelta(seconds=LEASE_SECONDS)},
    ).all()
    return [row[0] for row in rows]


def _load(session: Session, delivery_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                """
            SELECT d.id, d.status, d.attempt_count, d.alert_event_id, c.id AS channel_id,
                   c.channel_type, c.destination_reference, c.status AS channel_status,
                   c.routing_rules
              FROM notification_deliveries d
              JOIN notification_channels c ON c.id = d.channel_id
             WHERE d.id = :id
            """
            ),
            {"id": delivery_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _load_message(session: Session, alert_id: uuid.UUID) -> render.AlertMessage | None:
    row = (
        session.execute(
            text(
                """
            SELECT id, rule_name, severity, status, environment, summary, target_type,
                   target_id, first_seen_at
              FROM alert_events WHERE id = :id
            """
            ),
            {"id": alert_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return render.AlertMessage(
        alert_id=row["id"],
        rule_name=row["rule_name"],
        severity=row["severity"],
        status=row["status"],
        environment=row["environment"],
        summary=row["summary"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        first_seen_at=row["first_seen_at"],
        link=render.link_for(get_settings().public_base_url, row["id"]),
    )


def record(
    session: Session,
    delivery_id: uuid.UUID,
    *,
    attempts_made: int,
    outcome: Outcome,
    now: datetime | None = None,
) -> str | None:
    """Write the result of one attempt; return the new status, or ``None`` if not applied.

    Guarded on ``status = 'PENDING'``: a row something else already finished is left alone.
    """
    moment = now or datetime.now(UTC)
    delays = get_settings().notification_retry_delays_seconds
    if outcome.kind == "sent":
        status, next_at, sent_at, error = "SENT", None, moment, None
    elif outcome.kind == "permanent":
        status, next_at, sent_at, error = "FAILED", None, None, outcome.reason
    else:
        delay = next_delay(delays, attempts_made, outcome.retry_after)
        if delay is None:
            status, next_at, sent_at, error = "ABANDONED", None, None, outcome.reason
        else:
            status, next_at, sent_at, error = (
                "PENDING",
                moment + timedelta(seconds=delay),
                None,
                outcome.reason,
            )
    applied = session.execute(
        text(
            """
            UPDATE notification_deliveries
               SET status = :status, next_attempt_at = :next_at, sent_at = :sent_at,
                   last_error = :error
             WHERE id = :id AND status = 'PENDING'
         RETURNING status
            """
        ),
        {
            "id": delivery_id,
            "status": status,
            "next_at": next_at,
            "sent_at": sent_at,
            "error": error,
        },
    ).scalar_one_or_none()
    return str(applied) if applied is not None else None


def process(
    session: Session,
    delivery_id: uuid.UUID,
    *,
    sender: Sender = default_sender,
    backend: secrets.SecretBackend | None = None,
) -> str:
    """Send one leased delivery. Returns what happened, as a closed word.

    ``skipped`` (not pending, or gone), or the status it was left in.
    """
    row = _load(session, delivery_id)
    if row is None or row["status"] != "PENDING":
        session.rollback()
        return "skipped"
    attempts = int(row["attempt_count"])
    channel_type = str(row["channel_type"])

    outcome: Outcome | None = None
    message: render.AlertMessage | None = None
    destination = ""
    if row["channel_status"] != "ACTIVE":
        # Re-checked at the moment of sending (rule 3's pattern): a channel disabled while the
        # task waited is honoured, and the row says why it never went out.
        outcome = classify_reason("channel_disabled")
    elif parse_stored_rules(row["routing_rules"]) is None:
        outcome = classify_reason("channel_rules_invalid")
    else:
        message = _load_message(session, row["alert_event_id"])
        if message is None:
            outcome = classify_reason("alert_missing")
        elif channel_type == "EMAIL":
            destination = str(row["destination_reference"])
        else:
            try:
                destination = secrets.resolve(
                    str(row["destination_reference"]), backend
                ).get_secret_value()
            except secrets.SecretUnresolvableError:
                outcome = classify_reason("secret_unresolvable")

    # No transaction is open while the network is touched.
    session.rollback()
    if outcome is None:
        assert message is not None
        outcome = sender(channel_type, destination, message)
    status = record(session, delivery_id, attempts_made=attempts, outcome=outcome)
    session.commit()
    logger.info(
        "notification_attempt",
        delivery_id=str(delivery_id),
        channel_type=channel_type,
        attempt=attempts,
        result=outcome.kind,
        reason=outcome.reason,
        status=status,
    )
    return status or "skipped"
