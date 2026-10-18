"""§20.1 - §20.4: reading alerts and moving them through the human half of their lifecycle.

Two halves, and they never touch each other's column. The **source** (Alertmanager, through
the webhook) opens, repeats and resolves an alert. A **person** acknowledges and assigns it.
Acknowledgement changes ``status`` and ``acknowledged_*`` and **never** ``resolved_at`` -- the
schema forbids the combination (``ck_alert_events_resolved_at``) and a test pins it -- so an
acknowledged alert is still an open one, and only the source can close it (PRD §4.8).

Every read is scoped in SQL (``app/alerts/queries.py``); an alert the caller may not read is
``404``, the same answer as one that does not exist (§2). Writes are one conditional
``UPDATE ... WHERE status = ...``, so two people acknowledging at once, or a person racing the
source's resolve, produce one winner and a ``409`` rather than a lost update.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import queries
from app.alerts.catalogue import NOT_PRODUCED_YET, RULES
from app.alerts.lifecycle import OPEN_STATUSES, can_transition
from app.alerts.models import AlertEvent
from app.alerts.schemas import (
    AlertDetail,
    AlertLinks,
    AlertList,
    AlertSummary,
    ConditionCoverage,
    HistoryEntry,
    PreviousOccurrence,
    TargetRef,
    UserRef,
)
from app.audit import service as audit
from app.audit.models import AuditEvent
from app.auth.models import User
from app.authz import service as authz_service
from app.authz.dependencies import Authorized
from app.authz.matcher import permits
from app.core.config import get_settings
from app.core.errors import field_error
from app.core.pagination import Cursor, decode_cursor, encode_cursor
from app.inventory.models import Server, Service
from app.inventory.schemas import CursorPage
from app.inventory.service import InvalidStateTransitionError, NotFoundError

#: Events written by people, read back for the timeline.
_HUMAN_ACTIONS = ("ALERT_ACKNOWLEDGED", "ALERT_ASSIGNED")


def coverage() -> ConditionCoverage:
    """What produces alerts and what does not yet: never imply a condition is monitored."""
    return ConditionCoverage(
        produced=sorted({rule.condition for rule in RULES}),
        not_produced_yet=[
            {"condition": condition, "arrives_in": milestone}
            for condition, milestone in NOT_PRODUCED_YET
        ],
    )


# --- shaping rows ---------------------------------------------------------------------------


async def _summaries(db: AsyncSession, rows: Sequence[AlertEvent]) -> list[AlertSummary]:
    """Rows to API objects, with the people and targets named by **lookup**."""
    user_ids = {
        user for row in rows for user in (row.acknowledged_by, row.assigned_to) if user is not None
    }
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        found_users = await db.execute(
            select(User.id, User.display_name).where(User.id.in_(user_ids))
        )
        names = dict(found_users.all())

    service_ids = {r.target_id for r in rows if r.target_type == "service" and r.target_id}
    services: dict[uuid.UUID, tuple[uuid.UUID, str, str | None]] = {}
    if service_ids:
        services = {
            sid: (server_id, service_name, display)
            for sid, server_id, service_name, display in (
                await db.execute(
                    select(
                        Service.id, Service.server_id, Service.service_name, Service.display_name
                    ).where(Service.id.in_(service_ids))
                )
            ).all()
        }
    server_ids = {r.target_id for r in rows if r.target_type == "server" and r.target_id} | {
        server_id for server_id, _, _ in services.values()
    }
    servers: dict[uuid.UUID, str] = {}
    if server_ids:
        found_servers = await db.execute(
            select(Server.id, Server.display_name).where(Server.id.in_(server_ids))
        )
        servers = dict(found_servers.all())

    def user(identifier: uuid.UUID | None) -> UserRef | None:
        if identifier is None:
            return None
        return UserRef(id=identifier, display_name=names.get(identifier, "unknown user"))

    out: list[AlertSummary] = []
    for row in rows:
        target: TargetRef | None = None
        links = AlertLinks()
        if row.target_type == "server" and row.target_id is not None:
            target = TargetRef(
                type="server", id=row.target_id, display_name=servers.get(row.target_id)
            )
            links = AlertLinks(server=f"/api/v1/servers/{row.target_id}")
        elif row.target_type == "service" and row.target_id is not None:
            found = services.get(row.target_id)
            target = TargetRef(
                type="service", id=row.target_id, display_name=found[2] if found else None
            )
            if found:
                links = AlertLinks(
                    server=f"/api/v1/servers/{found[0]}",
                    service=f"/api/v1/servers/{found[0]}/services/{found[1]}",
                )
        elif row.target_type is not None and row.target_id is not None:
            target = TargetRef(type=row.target_type, id=row.target_id, display_name=None)  # type: ignore[arg-type]
        out.append(
            AlertSummary(
                alert_id=row.id,
                source=row.source,  # type: ignore[arg-type]
                rule_name=row.rule_name,
                severity=row.severity,  # type: ignore[arg-type]
                status=row.status,  # type: ignore[arg-type]
                summary=row.summary,
                target=target,
                environment=row.environment,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                occurrence_count=row.occurrence_count,
                acknowledged_by=user(row.acknowledged_by),
                acknowledged_at=row.acknowledged_at,
                assigned_to=user(row.assigned_to),
                resolved_at=row.resolved_at,
                links=links,
            )
        )
    return out


# --- §20.1 -----------------------------------------------------------------------------------


def _fingerprint(**filters: Any) -> str:
    """Pins a cursor to the filters it was minted for, in the field that pins the sort."""
    encoded = json.dumps(filters, sort_keys=True, default=str, separators=(",", ":"))
    return "alerts:" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


async def list_alerts(
    db: AsyncSession,
    authorized: Authorized,
    *,
    statuses: Sequence[str],
    severities: Sequence[str] | None,
    environment: Sequence[str] | None,
    target_type: str | None,
    target_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    q: str | None,
    cursor_token: str | None,
    limit: int,
) -> AlertList:
    sort = _fingerprint(
        statuses=sorted(statuses),
        severities=sorted(severities or ()),
        environment=sorted(environment or ()),
        target_type=target_type,
        target_id=target_id,
        start=start,
        end=end,
        q=q,
    )
    cursor: Cursor | None = (
        decode_cursor(cursor_token, sort=sort, order="desc") if cursor_token else None
    )
    conditions = queries.filter_conditions(
        statuses=statuses,
        severities=severities,
        environment=environment,
        target_type=target_type,
        target_id=target_id,
        start=start,
        end=end,
        q=q,
    )
    rows = list(
        (
            await db.execute(
                queries.list_query(
                    authorized.grants, conditions=conditions, cursor=cursor, limit=limit
                )
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(key=page[-1].last_seen_at, row_id=page[-1].id, sort=sort, order="desc")
        if has_more and page
        else None
    )
    return AlertList(
        items=await _summaries(db, page),
        page=CursorPage(limit=limit, next_cursor=next_cursor, has_more=has_more),
        coverage=coverage(),
        demo_mode=get_settings().demo_mode,
    )


# --- §20.2 -----------------------------------------------------------------------------------


async def _visible(
    db: AsyncSession, grants: Sequence[Any], alert_id: uuid.UUID
) -> AlertEvent | None:
    result = await db.execute(queries.one_query(grants, alert_id))
    return result.scalar_one_or_none()


async def _history(db: AsyncSession, row: AlertEvent) -> list[HistoryEntry]:
    entries = [HistoryEntry(at=row.first_seen_at, event="first_seen")]
    human = (
        await db.execute(
            select(
                AuditEvent.occurred_at,
                AuditEvent.action_type,
                AuditEvent.actor_label,
                AuditEvent.sanitized_metadata,
            )
            .where(
                AuditEvent.target_type == "alert_event",
                AuditEvent.target_id == row.id,
                AuditEvent.action_type.in_(_HUMAN_ACTIONS),
                AuditEvent.outcome == "SUCCESS",
            )
            .order_by(AuditEvent.occurred_at)
        )
    ).all()
    for at, action, actor, metadata in cast(
        "list[tuple[datetime, str, str, dict[str, Any]]]", human
    ):
        note = metadata.get("note") if isinstance(metadata, dict) else None
        if action == "ALERT_ACKNOWLEDGED":
            entries.append(HistoryEntry(at=at, event="acknowledged", actor=actor, note=note))
        else:
            assigned = isinstance(metadata, dict) and metadata.get("assignee_id")
            entries.append(
                HistoryEntry(
                    at=at,
                    event="assigned" if assigned else "unassigned",
                    actor=actor,
                    subject=metadata.get("assignee_name") if assigned else None,
                    note=note,
                )
            )
    if row.resolved_at is not None:
        entries.append(HistoryEntry(at=row.resolved_at, event="resolved"))
    if row.status == "EXPIRED":
        # An expired alert accepts no further change, so its last update is its expiry.
        entries.append(HistoryEntry(at=row.updated_at, event="expired"))
    return sorted(entries, key=lambda entry: entry.at)


async def detail(db: AsyncSession, authorized: Authorized, alert_id: uuid.UUID) -> AlertDetail:
    row = await _visible(db, authorized.grants, alert_id)
    if row is None:
        raise NotFoundError
    [summary] = await _summaries(db, [row])
    previous = (
        (
            await db.execute(
                select(AlertEvent)
                .where(
                    AlertEvent.dedup_key == row.dedup_key,
                    AlertEvent.id != row.id,
                    AlertEvent.status.in_(("RESOLVED", "EXPIRED")),
                )
                .order_by(AlertEvent.first_seen_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return AlertDetail(
        **summary.model_dump(),
        details=row.details,
        history=await _history(db, row),
        previous_occurrences=[
            PreviousOccurrence(
                alert_id=p.id,
                status=p.status,  # type: ignore[arg-type]
                first_seen_at=p.first_seen_at,
                last_seen_at=p.last_seen_at,
                resolved_at=p.resolved_at,
                occurrence_count=p.occurrence_count,
            )
            for p in previous
        ],
    )


# --- §20.3, §20.4 ----------------------------------------------------------------------------


def _changed(result: object) -> bool:
    return cast(CursorResult[Any], result).rowcount == 1


async def acknowledge(
    db: AsyncSession,
    authorized: Authorized,
    alert_id: uuid.UUID,
    *,
    note: str | None,
    client_ip: str | None,
    user_agent: str | None,
) -> AlertDetail:
    """FIRING to ACKNOWLEDGED. Never touches ``resolved_at``; the source resolves."""
    row = await _visible(db, authorized.grants, alert_id)
    if row is None:
        raise NotFoundError
    if not can_transition(row.status, "ACKNOWLEDGED"):
        raise _illegal(row.status)

    changed = await db.execute(
        update(AlertEvent)
        .where(
            AlertEvent.id == row.id,
            AlertEvent.first_seen_at == row.first_seen_at,
            AlertEvent.status == "FIRING",
        )
        .values(
            status="ACKNOWLEDGED",
            acknowledged_by=authorized.user.id,
            acknowledged_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    )
    if not _changed(changed):
        # Lost a race with another acknowledgement or with the source's resolve.
        raise _illegal("changed concurrently")
    audit.record(
        db,
        action_type="ALERT_ACKNOWLEDGED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="alert_event",
        target_id=row.id,
        environment=row.environment,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={"rule_name": row.rule_name, "severity": row.severity, "note": note},
    )
    await db.flush()
    db.expire_all()
    return await detail(db, authorized, alert_id)


async def assign(
    db: AsyncSession,
    authorized: Authorized,
    alert_id: uuid.UUID,
    *,
    assignee_id: uuid.UUID | None,
    note: str | None,
    client_ip: str | None,
    user_agent: str | None,
) -> AlertDetail:
    """Assign an open alert to a user who could act on it, or unassign it."""
    row = await _visible(db, authorized.grants, alert_id)
    if row is None:
        raise NotFoundError
    if row.status not in OPEN_STATUSES:
        raise _illegal(row.status)

    assignee_name: str | None = None
    if assignee_id is not None:
        assignee = await db.get(User, assignee_id)
        grants = await authz_service.resolve_grants(db, assignee_id) if assignee else []
        # An active user who holds `alert:acknowledge` and whose scope reaches *this* alert.
        # The same refusal for every failure, so the endpoint is not a way to probe users.
        if (
            assignee is None
            or assignee.status != "ACTIVE"
            or not permits(grants, "alert", "acknowledge").allowed
            or await _visible(db, grants, alert_id) is None
        ):
            raise field_error(
                ["body", "assignee_id"],
                "invalid_assignee",
                "That user cannot be assigned this alert.",
            )
        assignee_name = assignee.display_name

    changed = await db.execute(
        update(AlertEvent)
        .where(
            AlertEvent.id == row.id,
            AlertEvent.first_seen_at == row.first_seen_at,
            AlertEvent.status.in_(OPEN_STATUSES),
        )
        .values(assigned_to=assignee_id)
        .execution_options(synchronize_session=False)
    )
    if not _changed(changed):
        raise _illegal("changed concurrently")
    audit.record(
        db,
        action_type="ALERT_ASSIGNED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="alert_event",
        target_id=row.id,
        environment=row.environment,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={
            "assignee_id": str(assignee_id) if assignee_id else None,
            "assignee_name": assignee_name,
            "note": note,
        },
    )
    await db.flush()
    db.expire_all()
    return await detail(db, authorized, alert_id)


def _illegal(current: str) -> InvalidStateTransitionError:
    error = InvalidStateTransitionError()
    error.details = {"current_status": current}
    return error
