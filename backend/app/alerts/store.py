"""The alert write paths, once, over a synchronous ``Session`` (``OPEN-42``).

Synchronous on purpose, so one implementation serves both callers: the webhook runs it
through ``AsyncSession.run_sync`` and the worker tasks (expiry, the partition-runway
monitor) call it with a plain ``Session``. Two copies of an upsert that must hold an
invariant are how the invariant stops holding.

**The invariant is "at most one open alert per ``dedup_key``", and the database holds
it**: ``alert_open_keys`` has the key as its primary key, a firing alert inserts the key
``ON CONFLICT DO NOTHING``, and a key that was inserted means "this is a new alert". The
key is deleted in the same transaction that closes the event, so a key without an open
event cannot outlive a transaction (and :func:`fire` repairs one if it ever does).

Each function works on one alert and **commits nothing**; the caller owns the
transaction. That is what lets the webhook commit alert by alert (so one bad alert cannot
roll back the others, and no key lock is held across a batch) while the worker commits
once.

Nothing here reads a payload. Severity, rule meaning and discriminators come from the
caller, which got them from the catalogue (``app/alerts/catalogue.py``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.alerts.models import AlertEvent, AlertOpenKey
from app.alerts.payload import TargetLabels
from app.db.types import uuid7
from app.inventory.models import Server, Service

OPEN = ("FIRING", "ACKNOWLEDGED")


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """What a lookup found, or nothing. ``environment`` is the *row's*, never a label's."""

    target_type: str | None
    target_id: uuid.UUID | None
    server_id: uuid.UUID | None
    environment: str | None

    @property
    def resolved(self) -> bool:
        return self.target_type is not None


UNRESOLVED = ResolvedTarget(None, None, None, None)


def resolve_target(session: Session, claim: TargetLabels, *, wants_unit: bool) -> ResolvedTarget:
    """Map claimed ids to live rows, or to nothing (SECURITY.md §21, ``OPEN-45``).

    A claim that matches no row, a retired row or a malformed one all give
    :data:`UNRESOLVED`: an unlinked alert, not a guess. ``environment`` comes from the
    server row, so a payload cannot place itself in a scope by saying where it is.
    """
    if claim.server_id is None:
        return UNRESOLVED
    server = session.execute(
        select(Server.id, Server.environment).where(
            Server.id == claim.server_id, Server.deleted_at.is_(None)
        )
    ).first()
    if server is None:
        return UNRESOLVED
    server_id, environment = server
    if wants_unit and claim.unit is not None:
        service_id = session.execute(
            select(Service.id).where(
                Service.server_id == server_id,
                Service.service_name == claim.unit,
                Service.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if service_id is not None:
            return ResolvedTarget("service", service_id, server_id, environment)
    return ResolvedTarget("server", server_id, server_id, environment)


def fire(
    session: Session,
    *,
    source: str,
    dedup: str,
    rule_name: str,
    severity: str,
    target: ResolvedTarget,
    summary: str,
    details: dict[str, Any],
    now: datetime,
) -> Literal["created", "updated"]:
    """Open an alert, or count a repeat of the one already open."""
    for _ in range(2):  # the second pass is the repair of a dangling key
        alert_id = uuid7()
        inserted = session.execute(
            pg_insert(AlertOpenKey)
            .values(dedup_key=dedup, alert_id=alert_id, first_seen_at=now)
            .on_conflict_do_nothing(index_elements=[AlertOpenKey.dedup_key])
            .returning(AlertOpenKey.alert_id)
        ).scalar_one_or_none()
        if inserted is not None:
            session.add(
                AlertEvent(
                    id=alert_id,
                    first_seen_at=now,
                    source=source,
                    dedup_key=dedup,
                    rule_name=rule_name,
                    target_type=target.target_type,
                    target_id=target.target_id,
                    environment=target.environment,
                    severity=severity,
                    status="FIRING",
                    summary=summary,
                    details=details,
                    occurrence_count=1,
                    last_seen_at=now,
                )
            )
            session.flush()
            return "created"

        open_key = session.execute(
            select(AlertOpenKey.alert_id, AlertOpenKey.first_seen_at).where(
                AlertOpenKey.dedup_key == dedup
            )
        ).one()
        updated = session.execute(
            update(AlertEvent)
            .where(
                AlertEvent.id == open_key.alert_id,
                AlertEvent.first_seen_at == open_key.first_seen_at,
                AlertEvent.status.in_(OPEN),
            )
            .values(
                occurrence_count=AlertEvent.occurrence_count + 1,
                last_seen_at=now,
                summary=summary,
                details=details,
            )
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], updated).rowcount == 1:
            return "updated"
        # A key that points at no open event: remove it and open the alert properly.
        session.execute(delete(AlertOpenKey).where(AlertOpenKey.dedup_key == dedup))
    raise RuntimeError("could not open or update an alert")  # pragma: no cover


def resolve(session: Session, *, dedup: str, now: datetime) -> Literal["resolved", "ignored"]:
    """Close the open alert for ``dedup``; ``ignored`` if there is none.

    A ``resolved`` for something DCP never opened (or already expired) creates nothing:
    a row born resolved would be a history entry for a problem nobody saw.
    """
    open_key = session.execute(
        select(AlertOpenKey.alert_id, AlertOpenKey.first_seen_at).where(
            AlertOpenKey.dedup_key == dedup
        )
    ).first()
    if open_key is None:
        return "ignored"
    closed = session.execute(
        update(AlertEvent)
        .where(
            AlertEvent.id == open_key.alert_id,
            AlertEvent.first_seen_at == open_key.first_seen_at,
            AlertEvent.status.in_(OPEN),
        )
        .values(status="RESOLVED", resolved_at=now, last_seen_at=now)
        .execution_options(synchronize_session=False)
    )
    session.execute(delete(AlertOpenKey).where(AlertOpenKey.dedup_key == dedup))
    return "resolved" if cast(CursorResult[Any], closed).rowcount == 1 else "ignored"


def expire(session: Session, *, after_seconds: int, now: datetime | None = None) -> int:
    """Mark open alerts not refreshed within ``after_seconds`` ``EXPIRED`` (``OPEN-44``).

    ``EXPIRED`` is "not confirmed either way": the source never said the problem
    cleared, and it stopped saying it was there. One statement, so the status change
    and the key's deletion cannot be separated.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=after_seconds)
    result = session.execute(
        text(
            """
            WITH expired AS (
                UPDATE alert_events SET status = 'EXPIRED'
                 WHERE status IN ('FIRING', 'ACKNOWLEDGED') AND last_seen_at < :cutoff
             RETURNING id, dedup_key
            )
            DELETE FROM alert_open_keys k USING expired e
             WHERE k.dedup_key = e.dedup_key AND k.alert_id = e.id
            """
        ),
        {"cutoff": cutoff},
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)
