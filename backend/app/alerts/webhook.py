"""§20.5: the Alertmanager webhook -- the first inbound path from a lower-trust component.

There is **no session and no permission here**, so this is one of the explicit exceptions
in ``tests/authz/test_route_coverage.py``, and everything that normally protects a route is
done by hand, in this order and failing closed at each step (SECURITY.md §21, ``OPEN-43``):

1. **Configured?** No ``DCP_ALERT_WEBHOOK_SECRET`` means ``503 not_configured``, never open.
2. **Source address.** The TCP peer only -- ``X-Forwarded-For`` is never read -- against
   ``DCP_ALERT_WEBHOOK_ALLOWED_CIDRS``. Empty denies everything.
3. **Rate limit**, keyed on the peer and applied *before* the secret is compared, so a
   guessing run is throttled. It fails open when Redis is down: losing alerts to protect a
   budget would be the wrong trade, and the secret is still required.
4. **Secret**: ``Authorization: Bearer``, compared in constant time.
5. **Body**: read with a hard ceiling *while streaming* (the server imposes none), then
   parsed strictly. A wholly invalid body is a ``4xx``; an individually bad *alert* is a
   per-alert rejection inside a ``202`` -- measured against a real Alertmanager, a ``4xx``
   is re-sent every ``group_interval`` forever, so it must be reserved for what only a
   misconfiguration can cause.

Every refusal is counted in Redis (``app/alerts/liveness.py``) as well as logged: a smoke
test asserts the counter, because "nothing was stored" is also what a dead endpoint shows.

Nothing in the payload can cause an operation: ``app/alerts/ingest.py`` has no operation to
run, and the content it stores is text.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import ingest, liveness
from app.alerts.payload import MAX_BODY_BYTES, WebhookEnvelope
from app.alerts.schemas import WebhookAlertResult, WebhookResponse
from app.audit import service as audit
from app.auth.dependencies import client_ip, user_agent
from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.errors import AppError, FieldValidationError, NotConfiguredError
from app.core.logging import get_logger
from app.core.redaction import redact_text
from app.db.session import get_session

logger = get_logger(__name__)

router = APIRouter(tags=["alerts"])

Db = Annotated[AsyncSession, Depends(get_session)]

#: Alertmanager sends a handful of deliveries a minute at most; this is headroom.
RATE_LIMIT: Final = 120
RATE_WINDOW_SECONDS: Final = 60
ACTOR_LABEL: Final = "system:alertmanager"


class WebhookForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    message = "Not permitted."


class WebhookUnauthenticatedError(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication required."

    def response_headers(self) -> dict[str, str]:
        return {"WWW-Authenticate": "Bearer"}


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The request body is too large."


async def _refuse(reason: str, error: AppError, request: Request) -> AppError:
    await liveness.note_refused(reason)
    logger.warning("alert_webhook_refused", reason=reason, peer=client_ip(request))
    return error


def _peer_allowed(peer: str | None, cidrs: tuple[str, ...]) -> bool:
    if peer is None or not cidrs:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in ipaddress.ip_network(entry, strict=False) for entry in cidrs)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PayloadTooLargeError
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise PayloadTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _envelope(raw: bytes) -> WebhookEnvelope:
    try:
        data: Any = json.loads(raw)
        return WebhookEnvelope.model_validate(data)
    except ValueError as error:
        if isinstance(error, ValidationError):
            fields = [
                {
                    "loc": ["body", *(str(part)[:64] for part in item["loc"])],
                    "code": str(item["type"]),
                    # Never ``item["input"]``: a rejected value is not echoed (§8).
                    "message": redact_text(str(item["msg"])),
                }
                for item in error.errors()
            ]
        else:
            fields = [{"loc": ["body"], "code": "invalid_json", "message": "Not valid JSON."}]
        raise FieldValidationError(fields) from None


async def _guard(request: Request, settings: Settings) -> None:
    secret = settings.alert_webhook_secret
    if secret is None:
        raise await _refuse("not_configured", NotConfiguredError(), request)
    if not _peer_allowed(client_ip(request), settings.alert_webhook_allowed_cidrs):
        raise await _refuse("source_address", WebhookForbiddenError(), request)
    try:
        await ratelimit.enforce(
            "alert_webhook",
            client_ip(request) or "unknown",
            limit=RATE_LIMIT,
            window_seconds=RATE_WINDOW_SECONDS,
            fail_closed=False,
        )
    except ratelimit.RateLimitedError as error:
        raise await _refuse("rate_limited", error, request) from None
    if not hmac.compare_digest(_bearer(request).encode(), secret.get_secret_value().encode()):
        raise await _refuse("bad_secret", WebhookUnauthenticatedError(), request)


@router.post(
    "/webhooks/alerts",
    status_code=202,
    summary="Alertmanager ingress (shared secret, not a session)",
    responses={
        401: {"description": "Missing or wrong shared secret."},
        403: {"description": "Source address not allowed."},
        413: {"description": "Body over the ceiling."},
        429: {"description": "Rate limited; Alertmanager re-sends at its group interval."},
        503: {"description": "No shared secret is configured."},
    },
)
async def receive_alerts(request: Request, db: Db) -> WebhookResponse:
    """Store what Alertmanager says is firing or resolved, as text, deduplicated."""
    settings = get_settings()
    await _guard(request, settings)
    try:
        envelope = _envelope(await _read_body(request))
    except PayloadTooLargeError:
        raise await _refuse("body_too_large", PayloadTooLargeError(), request) from None
    except FieldValidationError as error:
        raise await _refuse("invalid_body", error, request) from None

    result = await db.run_sync(ingest.process_batch, envelope)
    await liveness.note_delivery()
    if result.watchdog_seen:
        await liveness.note_watchdog()

    stored = [o for o in result.outcomes if o.action != "watchdog"]
    if stored:
        audit.record(
            db,
            action_type="ALERT_INGESTED",
            outcome="SUCCESS",
            actor_type="SYSTEM",
            actor_label=ACTOR_LABEL,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={
                "accepted": result.accepted,
                "rejected": result.rejections(),
                "truncated_by_sender": result.truncated_by_sender,
                "watchdog": result.watchdog_seen,
            },
        )
    return WebhookResponse(
        accepted=result.accepted,
        rejected=result.rejected,
        truncated_by_sender=result.truncated_by_sender,
        results=[
            WebhookAlertResult(
                index=o.index,
                accepted=o.accepted,
                action=o.action,  # type: ignore[arg-type]
                reason=o.reason,  # type: ignore[arg-type]
            )
            for o in result.outcomes
        ],
    )
