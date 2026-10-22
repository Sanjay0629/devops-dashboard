"""The audit export, endpoints 24.3 – 24.5 (docs/API_CONTRACTS.md §24, ``OPEN-112``).

All three carry ``audit:read`` as their route permission and ask for ``admin:integrations`` as
well inside :mod:`app.audit.exports` (an audited ``403`` if it is missing). The request is
``Idempotency-Key``-keyed, creates a row and returns at once; the worker builds the file; the file
can be downloaded once, by the person who asked.

``POST /audit-events/export`` creates an **export**, not an audit event: there is still no route
that writes, changes or deletes an event.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import exports
from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.db.session import get_session

router = APIRouter(tags=["audit"])

Db = Annotated[AsyncSession, Depends(get_session)]
ExportId = Annotated[uuid.UUID, Path(description="Audit export identifier")]
ReadAudit = Annotated[Authorized, require_permission("audit", "read")]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=64,
        description="Client-generated key, unique per logical intent (§7).",
    ),
]


class ExportRequest(BaseModel):
    """§24.3. A window and a format; there is nothing else to ask for."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: AwareDatetime = Field(alias="from")
    to: AwareDatetime
    format: Literal["jsonl"] = "jsonl"


class ExportAccepted(BaseModel):
    export_id: uuid.UUID
    status: str
    expires_at: datetime
    idempotent_replay: bool


class ExportStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    export_id: uuid.UUID
    status: str
    from_: datetime = Field(alias="from")
    to: datetime
    row_count: int | None
    size_bytes: int | None
    sha256: str | None
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    retrieved_at: datetime | None


def _origin(request: Request) -> exports.Origin:
    return exports.Origin(client_ip=client_ip(request), user_agent=user_agent(request))


@router.post(
    "/audit-events/export",
    status_code=202,
    summary="Request a bounded audit export",
    responses={200: {"description": "An idempotent replay: the original export, nothing re-run."}},
)
async def request_export(
    request: Request,
    response: Response,
    body: ExportRequest,
    idempotency_key: IdempotencyKey,
    db: Db,
    authorized: ReadAudit,
) -> ExportAccepted:
    """§24.3. ``202`` with an export id; the file is built on the ``maintenance`` queue."""
    export, replayed = await exports.request_export(
        db,
        authorized,
        _origin(request),
        start=body.from_,
        end=body.to,
        key=idempotency_key,
        path=request.url.path,
    )
    if replayed:
        response.status_code = 200
    else:
        await exports.enqueue_export(export.id)
    return ExportAccepted(
        export_id=export.id,
        status=export.status,
        expires_at=export.expires_at,
        idempotent_replay=replayed,
    )


@router.get(
    "/audit-events/exports/{export_id}",
    dependencies=[ReadBudget],
    summary="Audit export status",
)
async def export_status(
    request: Request, export_id: ExportId, db: Db, authorized: ReadAudit
) -> ExportStatus:
    """§24.4. The requester's own export; anyone else's is ``404``."""
    export = await exports.read_status(
        db, authorized, _origin(request), export_id, request.url.path
    )
    return ExportStatus.model_validate(
        {
            "export_id": export.id,
            "status": export.status,
            "from": export.from_at,
            "to": export.to_at,
            "row_count": export.row_count,
            "size_bytes": export.size_bytes,
            "sha256": export.sha256,
            "failure_code": export.failure_code,
            "created_at": export.created_at,
            "completed_at": export.completed_at,
            "expires_at": export.expires_at,
            "retrieved_at": export.retrieved_at,
        }
    )


@router.get(
    "/audit-events/exports/{export_id}/download",
    dependencies=[ReadBudget],
    summary="Download the export, once",
    response_class=Response,
    responses={200: {"content": {exports.MEDIA_TYPE: {}}, "description": "The jsonl file."}},
)
async def download_export(
    request: Request, export_id: ExportId, db: Db, authorized: ReadAudit
) -> Response:
    """§24.5. The file leaves exactly once; a second call, or anyone else's, is never a file."""
    spent = await exports.download(db, authorized, _origin(request), export_id, request.url.path)
    return Response(
        content=spent.content,
        media_type=exports.MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="audit-export-{export_id}.jsonl"',
            "Cache-Control": "no-store",
            "X-Content-SHA256": spent.sha256,
        },
    )
