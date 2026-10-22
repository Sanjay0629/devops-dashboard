"""Bounded export of the audit trail (docs/API_CONTRACTS.md §24.3 – §24.5, ``OPEN-112``).

Three halves, kept in one file so the arithmetic is shared:

* **The request** (async, API): re-checks both permissions, creates an ``audit_exports`` row
  ``QUEUED`` in the same transaction as ``AUDIT_EXPORT_REQUESTED``, and only then hands the **id**
  to the worker.
* **The build** (sync, worker): takes the id and nothing else, claims the row, **re-authorises the
  requester from the database** (a person demoted since asking gets no data), reads the visible
  events through the same query as §24.1 in keyset chunks, refuses rather than truncates, and
  stores the file.
* **The download** (async, API): one statement that spends the export and returns its bytes, so two
  downloads racing have one winner; ``AUDIT_EXPORTED`` is written here, because this is the moment
  data leaves.

A sweep ends what nobody collected (``READY`` past 24 h) and what a dead worker abandoned
(``QUEUED`` / ``RUNNING`` past 15 minutes) -- the latter matters because
``uq_audit_exports_one_active_per_requester`` would otherwise stop that person ever asking again.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.audit import read
from app.audit import service as audit
from app.audit.models import (
    EXPORT_MAX_BYTES,
    EXPORT_MAX_ROWS,
    EXPORT_MAX_WINDOW_DAYS,
    AuditExport,
)
from app.audit.query import AuditFilters, audit_query, next_cursor
from app.auth.models import User
from app.authz import service as authz_service
from app.authz.dependencies import Authorized, ForbiddenError
from app.authz.matcher import permits
from app.core.errors import AppError, field_error
from app.core.logging import get_logger
from app.core.window import Window
from app.inventory.service import ConflictError, NotFoundError

logger = get_logger(__name__)

TASK_NAME: Final = "dcp.audit.export"
BUILD_LABEL: Final = "system:audit-export"
SWEEP_LABEL: Final = "system:audit-export-sweeper"
#: A finished export is collectable for this long.
EXPORT_TTL: Final = timedelta(hours=24)
#: A request nobody finished -- the worker died, or the message was lost -- is failed after this.
STALE_AFTER: Final = timedelta(minutes=15)
#: Events read per query while building; memory is bounded by ``EXPORT_MAX_BYTES`` regardless.
CHUNK: Final = 200  # the read query's own page ceiling
SWEEP_BATCH: Final = 100
MEDIA_TYPE: Final = "application/x-ndjson"


class ExportInProgressError(AppError):
    status_code = 409
    code = "export_in_progress"
    message = "You already have an audit export in progress."


class ExportNotAvailableError(AppError):
    status_code = 409
    code = "export_not_available"
    message = (
        "This export cannot be downloaded: it is not ready, was already downloaded, or expired."
    )


@dataclass(frozen=True, slots=True)
class Origin:
    client_ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class Download:
    content: bytes
    export_id: uuid.UUID
    row_count: int
    size_bytes: int
    sha256: str


def _constraint(error: IntegrityError) -> str | None:
    name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    return name if isinstance(name, str) else None


def _holds_both(authorized: Authorized) -> bool:
    return authorized.permits("audit", "read") and authorized.permits("admin", "integrations")


async def _deny(
    db: AsyncSession, authorized: Authorized, origin: Origin, method: str, path: str
) -> None:
    authz_service.record_denial(
        db,
        user_id=authorized.user.id,
        user_label=authorized.user.email,
        permission="admin:integrations",
        reason="no_matching_grant",
        method=method,
        path=path,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        target_type="audit",
    )
    await db.commit()
    raise ForbiddenError


def validate_window(start: datetime, end: datetime) -> None:
    """§24.3: at most 31 days, forwards. Both ends carry an offset (the model guarantees it)."""
    if start >= end:
        raise field_error(["body", "from"], "from_after_to", "'from' must be before 'to'.")
    if end - start > timedelta(days=EXPORT_MAX_WINDOW_DAYS):
        raise field_error(
            ["body", "from"],
            "range_too_large",
            f"An export is limited to {EXPORT_MAX_WINDOW_DAYS} days.",
        )


# --- the request ------------------------------------------------------------------------------


async def _by_key(db: AsyncSession, user_id: uuid.UUID, key: str) -> AuditExport | None:
    return (
        await db.scalars(
            select(AuditExport).where(
                AuditExport.requested_by == user_id, AuditExport.idempotency_key == key
            )
        )
    ).first()


def _same_request(row: AuditExport, start: datetime, end: datetime) -> bool:
    return row.from_at == start and row.to_at == end


def _reused() -> ConflictError:
    error = ConflictError("This Idempotency-Key was already used with a different request.")
    error.details = {"reason": "idempotency_key_reused_with_different_payload"}
    return error


async def request_export(
    db: AsyncSession,
    authorized: Authorized,
    origin: Origin,
    *,
    start: datetime,
    end: datetime,
    key: str,
    path: str,
) -> tuple[AuditExport, bool]:
    """Create a ``QUEUED`` export, or return the one this key already made."""
    if not _holds_both(authorized):
        await _deny(db, authorized, origin, "POST", path)
    validate_window(start, end)
    user = authorized.user.id

    existing = await _by_key(db, user, key)
    if existing is not None:
        if not _same_request(existing, start, end):
            raise _reused()
        return existing, True

    now = datetime.now(UTC)
    export = AuditExport(
        requested_by=user,
        idempotency_key=key,
        format="jsonl",
        from_at=start,
        to_at=end,
        status="QUEUED",
        created_at=now,
        expires_at=now + EXPORT_TTL,
    )
    db.add(export)
    try:
        await db.flush()
    except IntegrityError as integrity:
        constraint = _constraint(integrity)
        await db.rollback()
        # Two submissions of one key raced, or one is already in flight.
        raced = await _by_key(db, user, key)
        if raced is not None:
            if not _same_request(raced, start, end):
                raise _reused() from integrity
            return raced, True
        if constraint == "uq_audit_exports_one_active_per_requester":
            raise ExportInProgressError from integrity
        raise
    audit.record(
        db,
        action_type="AUDIT_EXPORT_REQUESTED",
        outcome="SUCCESS",
        actor_id=user,
        actor_label=authorized.user.email,
        target_type="audit",
        target_id=export.id,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata={
            "export_id": str(export.id),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "format": "jsonl",
        },
    )
    await db.commit()
    return export, False


async def enqueue_export(export_id: uuid.UUID) -> None:
    """Hand the id to the worker -- best effort, after the commit, and an id and nothing else.

    A broker that cannot be reached must not fail a request that already succeeded; the sweep
    fails an export that never started, and the requester asks again.
    """

    def publish() -> None:
        from app.workers.celery_app import celery_app

        celery_app.send_task(TASK_NAME, args=[str(export_id)], queue="maintenance", retry=False)

    try:
        await asyncio.to_thread(publish)
    except Exception as error:
        logger.warning("audit_export_enqueue_failed", error=type(error).__name__)


# --- status and download ---------------------------------------------------------------------


async def read_status(
    db: AsyncSession, authorized: Authorized, origin: Origin, export_id: uuid.UUID, path: str
) -> AuditExport:
    """§24.4. The requester's own export; anyone else's, or an unknown id, is ``404``."""
    if not _holds_both(authorized):
        await _deny(db, authorized, origin, "GET", path)
    export = await db.get(AuditExport, export_id)
    if export is None or export.requested_by != authorized.user.id:
        raise NotFoundError
    return export


#: Spends the export and returns what it held. ``UPDATE ... RETURNING`` yields the *new* row, which
#: has no content by design, so the old bytes come from a locked sub-select: a second download
#: blocks on the lock, then re-evaluates ``status = 'READY'`` against the committed row and finds
#: nothing. That re-evaluation is the whole guarantee.
_SPEND = text(
    """
    UPDATE audit_exports AS e
       SET status = 'RETRIEVED', retrieved_at = now(), content = NULL
      FROM (SELECT id, content FROM audit_exports
             WHERE id = :id AND requested_by = :requester
               AND status = 'READY' AND expires_at > now()
               FOR UPDATE) AS old
     WHERE e.id = old.id
    RETURNING old.content AS content, e.row_count AS row_count, e.size_bytes AS size_bytes,
              e.sha256 AS sha256
    """
)


async def download(
    db: AsyncSession, authorized: Authorized, origin: Origin, export_id: uuid.UUID, path: str
) -> Download:
    """§24.5. Once, to the requester, while ``READY``; otherwise never a file."""
    if not _holds_both(authorized):
        await _deny(db, authorized, origin, "GET", path)
    export = await db.get(AuditExport, export_id)
    if export is None or export.requested_by != authorized.user.id:
        raise NotFoundError
    spent = (
        (await db.execute(_SPEND, {"id": export_id, "requester": authorized.user.id}))
        .mappings()
        .first()
    )
    if spent is None:
        await db.rollback()
        raise ExportNotAvailableError
    audit.record(
        db,
        action_type="AUDIT_EXPORTED",
        outcome="SUCCESS",
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="audit",
        target_id=export_id,
        client_ip=origin.client_ip,
        user_agent=origin.user_agent,
        metadata={
            "export_id": str(export_id),
            "row_count": spent["row_count"],
            "size_bytes": spent["size_bytes"],
            "sha256": spent["sha256"],
        },
    )
    # Explicit: if the audit row cannot be written, the export is not spent and nothing leaves.
    await db.commit()
    return Download(
        content=bytes(spent["content"]),
        export_id=export_id,
        row_count=spent["row_count"],
        size_bytes=spent["size_bytes"],
        sha256=spent["sha256"],
    )


# --- the build (worker) -----------------------------------------------------------------------


def _system_event(
    session: Session,
    action: str,
    export_id: uuid.UUID,
    requester: uuid.UUID,
    label: str,
    **metadata: Any,
) -> None:
    audit.record(
        session,
        action_type=action,
        outcome="FAILURE" if action.endswith("FAILED") else "SUCCESS",
        actor_type="SYSTEM",
        actor_label=label,
        target_type="audit",
        target_id=export_id,
        metadata={"export_id": str(export_id), "on_behalf_of": str(requester), **metadata},
    )


def _fail(engine: Engine, export_id: uuid.UUID, requester: uuid.UUID, code: str) -> bool:
    """End a ``RUNNING`` export as ``FAILED``. ``False`` if something else got there first."""
    with Session(engine) as session:
        moved = session.execute(
            text(
                "UPDATE audit_exports SET status = 'FAILED', failure_code = :code,"
                " completed_at = now(), content = NULL"
                " WHERE id = :id AND status = 'RUNNING' RETURNING id"
            ),
            {"id": export_id, "code": code},
        ).first()
        if moved is None:
            session.rollback()
            return False
        _system_event(session, "AUDIT_EXPORT_FAILED", export_id, requester, BUILD_LABEL, code=code)
        session.commit()
    return True


def build(engine: Engine, export_id: uuid.UUID) -> str:
    """Produce the file for one export. Returns a short outcome word for the task's log line."""
    with Session(engine) as session:
        claimed = session.execute(
            text(
                "UPDATE audit_exports SET status = 'RUNNING'"
                " WHERE id = :id AND status = 'QUEUED'"
                " RETURNING requested_by, from_at, to_at"
            ),
            {"id": export_id},
        ).first()
        if claimed is None:
            session.rollback()
            return "not_queued"
        session.commit()
    requester: uuid.UUID = claimed[0]
    start: datetime = claimed[1]
    end: datetime = claimed[2]

    try:
        with Session(engine) as session:
            user = session.get(User, requester)
            grants = authz_service.resolve_grants_sync(session, requester)
            standing = (
                user is not None
                and user.status == "ACTIVE"
                and permits(grants, "audit", "read").allowed
                and permits(grants, "admin", "integrations").allowed
            )
            if not standing:
                _fail(engine, export_id, requester, "requester_not_authorised")
                return "requester_not_authorised"

            lines: list[bytes] = []
            size = 0
            token: str | None = None
            filters = AuditFilters()
            while True:
                rows = list(
                    session.scalars(
                        audit_query(
                            grants,
                            filters,
                            Window(start=start, end=end),
                            order="asc",
                            cursor_token=token,
                            limit=CHUNK,
                        )
                    )
                )
                page = rows[:CHUNK]
                for event in page:
                    line = read.view(event).model_dump_json().encode("utf-8") + b"\n"
                    size += len(line)
                    lines.append(line)
                    if len(lines) > EXPORT_MAX_ROWS or size > EXPORT_MAX_BYTES:
                        # Never truncated: a partial file that looks complete is worse than none.
                        _fail(engine, export_id, requester, "export_too_large")
                        return "export_too_large"
                if len(rows) <= CHUNK:
                    break
                token = next_cursor(page[-1], filters, order="asc")

            content = b"".join(lines)
            digest = hashlib.sha256(content).hexdigest()
            done = session.execute(
                text(
                    "UPDATE audit_exports SET status = 'READY', content = :content,"
                    " row_count = :rows, size_bytes = :size, sha256 = :sha, completed_at = now()"
                    " WHERE id = :id AND status = 'RUNNING' RETURNING id"
                ),
                {
                    "id": export_id,
                    "content": content,
                    "rows": len(lines),
                    "size": len(content),
                    "sha": digest,
                },
            ).first()
            if done is None:
                session.rollback()  # the sweep failed it while we worked; keep nothing
                return "superseded"
            session.commit()
        return "ready"
    except Exception:
        logger.exception("audit_export_failed")
        _fail(engine, export_id, requester, "internal_error")
        return "internal_error"


# --- the sweep (worker) -----------------------------------------------------------------------


def sweep(engine: Engine) -> dict[str, int]:
    """End what nobody collected and what a dead worker abandoned."""
    counts = {"expired": 0, "failed": 0}
    with Session(engine) as session:
        expired = session.execute(
            text(
                "UPDATE audit_exports SET status = 'EXPIRED', content = NULL"
                " WHERE id IN (SELECT id FROM audit_exports"
                "               WHERE status = 'READY' AND expires_at <= now()"
                "               ORDER BY expires_at LIMIT :n FOR UPDATE SKIP LOCKED)"
                " RETURNING id, requested_by"
            ),
            {"n": SWEEP_BATCH},
        ).all()
        for row in expired:
            _system_event(session, "AUDIT_EXPORT_EXPIRED", row[0], row[1], SWEEP_LABEL)
        stale = session.execute(
            text(
                "UPDATE audit_exports SET status = 'FAILED', failure_code = 'internal_error',"
                " completed_at = now(), content = NULL"
                " WHERE id IN (SELECT id FROM audit_exports"
                "               WHERE status IN ('QUEUED', 'RUNNING')"
                "                 AND created_at <= now() - make_interval(secs => :secs)"
                "               ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED)"
                " RETURNING id, requested_by"
            ),
            {"n": SWEEP_BATCH, "secs": STALE_AFTER.total_seconds()},
        ).all()
        for row in stale:
            _system_event(
                session,
                "AUDIT_EXPORT_FAILED",
                row[0],
                row[1],
                SWEEP_LABEL,
                code="internal_error",
                reason="abandoned",
            )
        session.commit()
    counts["expired"], counts["failed"] = len(expired), len(stale)
    return counts
