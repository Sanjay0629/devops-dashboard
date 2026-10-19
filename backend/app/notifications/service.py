"""Channel administration, delivery visibility and manual retry (``OPEN-52`` - ``OPEN-56``).

Writes validate a destination **at configuration time** (SECURITY.md §19 rule 2, "validated
at configuration time and at request time"): the reference must be well formed, must resolve
in this process, and the URL it resolves to must pass the allowlist. The value is never
echoed, logged or stored here; the worker resolves and re-validates it again at send time.

Audit rows carry the *names* of the fields that changed and the channel's type, never the
reference and never a value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.authz.dependencies import Authorized
from app.core import secrets
from app.core.config import get_settings
from app.core.errors import field_error
from app.core.ssrf import UrlNotAllowedError, validate_registered_url
from app.inventory.service import (
    ConflictError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.notifications import destination
from app.notifications.models import RETRYABLE_STATUSES, NotificationChannel, NotificationDelivery
from app.notifications.rules import parse_stored_rules
from app.notifications.schemas import (
    ChannelCreate,
    ChannelList,
    ChannelOut,
    ChannelUpdate,
    DeliveryHealth,
    DeliveryOut,
)

AUDIT_CHANGED = "NOTIFICATION_CHANNEL_CHANGED"
AUDIT_RETRY = "NOTIFICATION_RETRY_REQUESTED"


@dataclass(frozen=True, slots=True)
class Health:
    pending: int = 0
    sent_24h: int = 0
    failed_24h: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    #: Newest *finished* delivery: ``SENT``, ``FAILED`` or ``ABANDONED``, and its reason.
    newest_finished: str | None = None
    newest_finished_error: str | None = None
    attempted: bool = False


async def channel_health(db: AsyncSession) -> dict[uuid.UUID, Health]:
    """One pass over the delivery table, grouped by channel."""
    totals = (
        await db.execute(
            text(
                """
                SELECT channel_id,
                       count(*) FILTER (WHERE status = 'PENDING') AS pending,
                       count(*) FILTER (WHERE status = 'SENT'
                                        AND created_at > now() - interval '24 hours') AS sent_24h,
                       count(*) FILTER (WHERE status IN ('FAILED', 'ABANDONED')
                                        AND created_at > now() - interval '24 hours') AS failed_24h,
                       max(sent_at) AS last_success_at,
                       max(updated_at) FILTER (WHERE status IN ('FAILED', 'ABANDONED'))
                           AS last_failure_at,
                       bool_or(attempt_count > 0) AS attempted
                  FROM notification_deliveries GROUP BY channel_id
                """
            )
        )
    ).mappings()
    newest = {
        row["channel_id"]: row
        for row in (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT ON (channel_id) channel_id, status, last_error
                      FROM notification_deliveries
                     WHERE status IN ('SENT', 'FAILED', 'ABANDONED')
                     ORDER BY channel_id, updated_at DESC, id DESC
                    """
                )
            )
        ).mappings()
    }
    failures = {
        row["channel_id"]: row["last_error"]
        for row in (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT ON (channel_id) channel_id, last_error
                      FROM notification_deliveries
                     WHERE status IN ('FAILED', 'ABANDONED')
                     ORDER BY channel_id, updated_at DESC, id DESC
                    """
                )
            )
        ).mappings()
    }
    out: dict[uuid.UUID, Health] = {}
    for row in totals:
        latest = newest.get(row["channel_id"])
        out[row["channel_id"]] = Health(
            pending=row["pending"],
            sent_24h=row["sent_24h"],
            failed_24h=row["failed_24h"],
            last_success_at=row["last_success_at"],
            last_failure_at=row["last_failure_at"],
            last_error=failures.get(row["channel_id"]),
            newest_finished=latest["status"] if latest else None,
            newest_finished_error=latest["last_error"] if latest else None,
            attempted=bool(row["attempted"]),
        )
    return out


def _out(channel: NotificationChannel, health: Health) -> ChannelOut:
    return ChannelOut(
        id=channel.id,
        name=channel.name,
        channel_type=channel.channel_type,  # type: ignore[arg-type]
        destination_reference=channel.destination_reference,
        routing_rules=dict(channel.routing_rules),
        routing_rules_valid=parse_stored_rules(channel.routing_rules) is not None,
        status=channel.status,  # type: ignore[arg-type]
        created_at=channel.created_at,
        updated_at=channel.updated_at,
        deliveries=DeliveryHealth(
            pending=health.pending,
            sent_24h=health.sent_24h,
            failed_24h=health.failed_24h,
            last_success_at=health.last_success_at,
            last_failure_at=health.last_failure_at,
            last_error=health.last_error,
        ),
    )


async def list_channels(db: AsyncSession) -> ChannelList:
    channels = (
        (
            await db.execute(
                select(NotificationChannel).order_by(NotificationChannel.name).limit(200)
            )
        )
        .scalars()
        .all()
    )
    health = await channel_health(db)
    settings = get_settings()
    return ChannelList(
        items=[_out(c, health.get(c.id, Health())) for c in channels],
        email_available=settings.smtp_configured,
        demo_mode=settings.demo_mode,
    )


def check_destination(channel_type: str, reference: str) -> None:
    """Refuse a destination that could not be used, naming the rule and never the value."""
    try:
        destination.validate_destination(channel_type, reference)
    except destination.DestinationError as error:
        raise field_error(
            ["body", "destination_reference"],
            str(error.args[0]),
            "A destination secret belongs in the secret store: give a reference such as "
            "env://NAME, not the value."
            if error.args[0] == destination.SECRET_MISPLACED
            else "That is not a valid destination reference for this channel type.",
        ) from None
    if channel_type == "EMAIL":
        if not get_settings().smtp_configured:
            raise field_error(
                ["body", "channel_type"],
                "not_configured",
                "Email is not configured on this deployment (DCP_SMTP_HOST).",
            )
        return
    try:
        url = secrets.resolve(reference).get_secret_value()
    except secrets.SecretUnresolvableError:
        raise field_error(
            ["body", "destination_reference"],
            "reference_not_resolvable",
            "That reference does not resolve in this deployment's secret store.",
        ) from None
    try:
        validate_registered_url(url)
    except UrlNotAllowedError:
        raise field_error(
            ["body", "destination_reference"],
            "destination_not_allowed",
            "What that reference resolves to is not an allowed outbound destination.",
        ) from None


def _audit_change(
    db: AsyncSession,
    authorized: Authorized,
    channel: NotificationChannel,
    *,
    change: str,
    fields: list[str],
    client_ip: str | None,
    user_agent: str | None,
) -> None:
    audit.record(
        db,
        action_type=AUDIT_CHANGED,
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="notification_channel",
        target_id=channel.id,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={
            "change": change,
            "fields": sorted(fields),
            "channel_type": channel.channel_type,
            "status": channel.status,
        },
    )


async def _flush_unique(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise DuplicateResourceError(
            "A notification channel with that name already exists."
        ) from None


async def create_channel(
    db: AsyncSession,
    authorized: Authorized,
    body: ChannelCreate,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> ChannelOut:
    check_destination(body.channel_type, body.destination_reference)
    channel = NotificationChannel(
        name=body.name,
        channel_type=body.channel_type,
        destination_reference=body.destination_reference,
        routing_rules=body.routing_rules.model_dump(),
        status=body.status,
    )
    db.add(channel)
    await _flush_unique(db)
    _audit_change(
        db,
        authorized,
        channel,
        change="created",
        fields=["name", "destination_reference", "routing_rules", "status"],
        client_ip=client_ip,
        user_agent=user_agent,
    )
    await db.commit()
    await db.refresh(channel)
    return _out(channel, Health())


async def update_channel(
    db: AsyncSession,
    authorized: Authorized,
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> ChannelOut:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise NotFoundError
    changed = body.model_fields_set
    new_reference = body.destination_reference or channel.destination_reference
    new_status = body.status or channel.status
    if "destination_reference" in changed or (new_status == "ACTIVE" and "status" in changed):
        # A channel is checked when it is pointed somewhere and when it is switched on, so
        # a disabled channel with a stale reference cannot be re-enabled by accident.
        check_destination(channel.channel_type, new_reference)
    applied: list[str] = []
    if body.name is not None and body.name != channel.name:
        channel.name = body.name
        applied.append("name")
    if body.destination_reference is not None and new_reference != channel.destination_reference:
        channel.destination_reference = new_reference
        applied.append("destination_reference")
    if body.routing_rules is not None and body.routing_rules.model_dump() != channel.routing_rules:
        channel.routing_rules = body.routing_rules.model_dump()
        applied.append("routing_rules")
    if body.status is not None and body.status != channel.status:
        channel.status = body.status
        applied.append("status")
    if applied:
        await _flush_unique(db)
        _audit_change(
            db,
            authorized,
            channel,
            change="updated",
            fields=applied,
            client_ip=client_ip,
            user_agent=user_agent,
        )
    await db.commit()
    await db.refresh(channel)
    health = await channel_health(db)
    return _out(channel, health.get(channel.id, Health()))


# --- deliveries on an alert -----------------------------------------------------------------


def _delivery_out(delivery: NotificationDelivery, channel: NotificationChannel) -> DeliveryOut:
    return DeliveryOut(
        delivery_id=delivery.id,
        channel_id=channel.id,
        channel_name=channel.name,
        channel_type=channel.channel_type,  # type: ignore[arg-type]
        status=delivery.status,  # type: ignore[arg-type]
        attempt_count=delivery.attempt_count,
        last_error=delivery.last_error,
        next_attempt_at=delivery.next_attempt_at,
        sent_at=delivery.sent_at,
        created_at=delivery.created_at,
        retryable=delivery.status in RETRYABLE_STATUSES and channel.status == "ACTIVE",
    )


async def deliveries_for_alert(db: AsyncSession, alert_id: uuid.UUID) -> list[DeliveryOut]:
    rows = (
        await db.execute(
            select(NotificationDelivery, NotificationChannel)
            .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
            .where(NotificationDelivery.alert_event_id == alert_id)
            .order_by(NotificationDelivery.created_at, NotificationChannel.name)
        )
    ).all()
    return [_delivery_out(d, c) for d, c in rows]


async def active_channel_count(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                text("SELECT count(*) FROM notification_channels WHERE status = 'ACTIVE'")
            )
        ).scalar_one()
    )


async def retry_delivery(
    db: AsyncSession,
    authorized: Authorized,
    *,
    alert_id: uuid.UUID,
    delivery_id: uuid.UUID,
    client_ip: str | None,
    user_agent: str | None,
) -> DeliveryOut:
    """Make a ``FAILED`` / ``ABANDONED`` delivery due now, with a fresh attempt budget.

    The caller has already established that ``alert_id`` is readable by the actor; a delivery
    that belongs to a different alert is the same ``404`` as one that does not exist.
    """
    found = (
        await db.execute(
            select(NotificationDelivery, NotificationChannel)
            .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
            .where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.alert_event_id == alert_id,
            )
        )
    ).first()
    if found is None:
        raise NotFoundError
    delivery, channel = found
    if delivery.status not in RETRYABLE_STATUSES:
        error = InvalidStateTransitionError()
        error.details = {"current_status": delivery.status}
        raise error
    if channel.status != "ACTIVE":
        error2 = ConflictError("The channel is disabled; enable it before retrying.")
        error2.details = {"reason": "channel_disabled"}
        raise error2

    previous = {"status": delivery.status, "attempt_count": delivery.attempt_count}
    updated = await db.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.status.in_(RETRYABLE_STATUSES),
        )
        .values(status="PENDING", attempt_count=0, next_attempt_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], updated).rowcount != 1:
        # Lost a race with another retry: one winner, one audit row.
        await db.rollback()
        error3 = InvalidStateTransitionError()
        error3.details = {"current_status": "changed concurrently"}
        raise error3
    audit.record(
        db,
        action_type=AUDIT_RETRY,
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="notification_delivery",
        target_id=delivery_id,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={
            "alert_id": str(alert_id),
            "channel_id": str(channel.id),
            "previous_status": previous["status"],
            "previous_attempt_count": previous["attempt_count"],
        },
    )
    await db.commit()
    db.expire_all()
    refreshed = (
        await db.execute(
            select(NotificationDelivery, NotificationChannel)
            .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
            .where(NotificationDelivery.id == delivery_id)
        )
    ).one()
    return _delivery_out(*refreshed)
