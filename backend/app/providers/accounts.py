"""Cloud accounts: §26.2 list, §26.3 register, §26.4 edit, §26.5 verify, §26.6 sync request.

(``OPEN-86`` – ``OPEN-88``.) A reference is shown, never a value: for AWS it is a role ARN, which
is an identifier and not a credential, and anything that is not an ARN is refused without being
repeated. ``DISABLED`` is already enforced by the request gate and the worker; this adds the
route that sets it. Verification is a read-only probe whose result is cached for §26.1.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.authz.dependencies import Authorized
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import field_error
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.inventory.models import CloudAccount
from app.inventory.service import DuplicateResourceError, NotFoundError
from app.operations.guard import OperationInProgressError
from app.providers import registry
from app.providers.aws import accounts as aws_accounts
from app.providers.aws.adapter import AwsSsmAdapter
from app.providers.aws.api import target_from
from app.providers.errors import NotConfigured, ProviderError, Timeout, to_app_error
from app.providers.schemas import (
    AccountCreate,
    AccountList,
    AccountOut,
    AccountStatus,
    AccountUpdate,
    SyncAccepted,
    VerificationOut,
)

logger = get_logger(__name__)

VERIFICATION_TTL_SECONDS = 3_600
VERIFY_BOUND_SECONDS = 15.0
SYNC_TASK = "dcp.cloud.sync_account"
_KEY = "dcp:cloud-account:verification:"
#: Advisory-lock namespace for "a sync of this account is running" (``OPEN-88``).
SYNC_LOCK_NAMESPACE = 7431
_MAX_LISTED = 200


def fingerprint(reference: str | None) -> str | None:
    """A short digest of a reference, so a change is visible without showing a value."""
    if reference is None:
        return None
    return hashlib.sha256(reference.encode()).hexdigest()[:12]


# --- the cached verification --------------------------------------------------------------------


async def cached_verifications(ids: list[uuid.UUID]) -> dict[uuid.UUID, VerificationOut]:
    """Whatever Redis holds. Unreadable or malformed is simply absent: unknown, not fine."""
    if not ids:
        return {}
    try:
        values = await get_redis().mget([f"{_KEY}{i}" for i in ids])
    except RedisError:
        logger.warning("verification_cache_unreadable")
        return {}
    found: dict[uuid.UUID, VerificationOut] = {}
    for account_id, raw in zip(ids, values, strict=True):
        if raw is None:
            continue
        try:
            found[account_id] = VerificationOut.model_validate_json(raw)
        except ValidationError:
            continue
    return found


async def _remember(account_id: uuid.UUID, verification: VerificationOut) -> None:
    try:
        await get_redis().set(
            f"{_KEY}{account_id}", verification.model_dump_json(), ex=VERIFICATION_TTL_SECONDS
        )
    except RedisError:
        logger.warning("verification_cache_unwritable")


async def _forget(account_id: uuid.UUID) -> None:
    try:
        await get_redis().delete(f"{_KEY}{account_id}")
    except RedisError:
        logger.warning("verification_cache_unwritable")


# --- shapes --------------------------------------------------------------------------------------


def _out(account: CloudAccount, verification: VerificationOut | None) -> AccountOut:
    return AccountOut(
        id=account.id,
        provider=account.provider,
        account_reference=account.account_reference,
        display_name=account.display_name,
        region_configuration=dict(account.region_configuration or {}),
        credential_reference=account.credential_reference,
        credential_fingerprint=fingerprint(account.credential_reference),
        status=cast(AccountStatus, account.status),
        last_sync_at=account.last_sync_at,
        last_sync_error=account.last_sync_error,
        verification=verification,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _audit(
    db: AsyncSession,
    authorized: Authorized,
    action: str,
    account: CloudAccount,
    *,
    client_ip: str | None,
    user_agent: str | None,
    metadata: dict[str, Any],
    outcome: str = "SUCCESS",
) -> None:
    audit.record(
        db,
        action_type=action,
        outcome=outcome,
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="cloud_account",
        target_id=account.id,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={"provider": account.provider, **metadata},
    )


async def _load(db: AsyncSession, account_id: uuid.UUID) -> CloudAccount:
    account = (
        await db.execute(
            select(CloudAccount).where(
                CloudAccount.id == account_id, CloudAccount.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise NotFoundError
    return account


# --- §26.2 ---------------------------------------------------------------------------------------


async def list_accounts(db: AsyncSession) -> AccountList:
    accounts = (
        (
            await db.execute(
                select(CloudAccount)
                .where(CloudAccount.deleted_at.is_(None))
                .order_by(CloudAccount.display_name, CloudAccount.id)
                .limit(_MAX_LISTED)
            )
        )
        .scalars()
        .all()
    )
    cached = await cached_verifications([a.id for a in accounts])
    return AccountList(items=[_out(a, cached.get(a.id)) for a in accounts])


# --- §26.3 / §26.4 -------------------------------------------------------------------------------


def _check_registration(
    provider: str, account_reference: str, credential_reference: str, regions: list[str]
) -> None:
    if provider not in registry.registered_providers():
        raise field_error(
            ["body", "provider"],
            "no_adapter_for_provider",
            "No adapter exists for this provider yet, so an account for it could not be used.",
        )
    if provider == "AWS":
        problem = aws_accounts.registration_problem(
            account_reference, credential_reference, regions
        )
        if problem is not None:
            location, code, message = problem
            raise field_error(location, code, message)


async def create_account(
    db: AsyncSession,
    authorized: Authorized,
    body: AccountCreate,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> AccountOut:
    _check_registration(
        body.provider,
        body.account_reference,
        body.credential_reference,
        body.region_configuration.allowed_regions,
    )
    account = CloudAccount(
        provider=body.provider,
        account_reference=body.account_reference,
        display_name=body.display_name,
        region_configuration=body.region_configuration.model_dump(),
        credential_reference=body.credential_reference,
        status="ACTIVE",
    )
    db.add(account)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise DuplicateResourceError("That cloud account is already registered.") from None
    _audit(
        db,
        authorized,
        "CLOUD_ACCOUNT_REGISTERED",
        account,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={
            "fields": [
                "account_reference",
                "display_name",
                "region_configuration",
                "credential_reference",
            ]
        },
    )
    await db.commit()
    await db.refresh(account)
    return _out(account, None)


async def update_account(
    db: AsyncSession,
    authorized: Authorized,
    account_id: uuid.UUID,
    body: AccountUpdate,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> AccountOut:
    account = await _load(db, account_id)
    applied: list[str] = []
    previous_status = account.status
    reference = body.credential_reference or account.credential_reference
    regions = (
        body.region_configuration.allowed_regions
        if body.region_configuration is not None
        else list((account.region_configuration or {}).get("allowed_regions", []))
    )
    if reference is not None and (
        body.credential_reference is not None or body.region_configuration is not None
    ):
        _check_registration(account.provider, account.account_reference, reference, regions)
    if body.display_name is not None and body.display_name != account.display_name:
        account.display_name = body.display_name
        applied.append("display_name")
    if (
        body.region_configuration is not None
        and body.region_configuration.model_dump() != account.region_configuration
    ):
        account.region_configuration = body.region_configuration.model_dump()
        applied.append("region_configuration")
    if (
        body.credential_reference is not None
        and body.credential_reference != account.credential_reference
    ):
        account.credential_reference = body.credential_reference
        applied.append("credential_reference")
    if body.status is not None and body.status != account.status:
        account.status = body.status
        applied.append("status")
    if applied:
        await db.flush()
        _audit(
            db,
            authorized,
            "CLOUD_ACCOUNT_UPDATED",
            account,
            client_ip=client_ip,
            user_agent=user_agent,
            metadata={"fields": sorted(applied), "status": account.status},
        )
        if "status" in applied and account.status == "DISABLED":
            _audit(
                db,
                authorized,
                "CLOUD_ACCOUNT_DISABLED",
                account,
                client_ip=client_ip,
                user_agent=user_agent,
                metadata={"previous_status": previous_status},
            )
    await db.commit()
    await db.refresh(account)
    if {"credential_reference", "region_configuration"} & set(applied):
        # What was verified is no longer what is configured.
        await _forget(account.id)
    cached = await cached_verifications([account.id])
    return _out(account, cached.get(account.id))


# --- §26.5 / §26.6 -------------------------------------------------------------------------------


def aws_adapter(provider: str) -> AwsSsmAdapter:
    """The AWS adapter, or ``NotConfigured`` (demonstration mode, no adapter, a static key,
    no document version). Account-level reads need the live adapter, not just a factory."""
    adapter = registry.resolve_adapter(provider)
    if adapter.demo:
        raise NotConfigured(adapter_id=adapter.adapter_id, detail_code="demonstration_mode")
    if not isinstance(adapter, AwsSsmAdapter):
        raise NotConfigured(adapter_id=adapter.adapter_id, detail_code="no_adapter_for_provider")
    return adapter


async def verify_account(
    db: AsyncSession,
    authorized: Authorized,
    account_id: uuid.UUID,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> VerificationOut:
    account = await _load(db, account_id)
    # Each verification is several AWS calls: bound how often one account can be probed.
    await ratelimit.enforce(
        "cloud-verify", str(account.id), limit=6, window_seconds=60, fail_closed=True
    )
    try:
        adapter = aws_adapter(account.provider)
        default_region = (account.region_configuration or {}).get("default_region")
        target = target_from(
            role_arn=account.credential_reference,
            region=default_region if isinstance(default_region, str) else None,
            account_reference=account.account_reference,
        )
        version = get_settings().aws_ssm_document_version
        if version is None:
            raise NotConfigured(
                adapter_id=adapter.adapter_id, detail_code="document_version_not_set"
            )
        report = await asyncio.wait_for(
            asyncio.to_thread(aws_accounts.verify, adapter.api, target, document_version=version),
            timeout=VERIFY_BOUND_SECONDS,
        )
    except TimeoutError:
        raise to_app_error(Timeout(adapter_id="aws_ssm", detail_code="verify_timed_out")) from None
    except ProviderError as error:
        raise to_app_error(error) from error
    verification = VerificationOut(
        reachable=report.reachable,
        permissions_ok=report.permissions_ok,
        missing_permissions=list(report.missing_permissions),
        not_probed=list(aws_accounts.NOT_PROBED),
        checked_at=datetime.now(UTC),
        message=report.message,
    )
    await _remember(account.id, verification)
    _audit(
        db,
        authorized,
        "CLOUD_ACCOUNT_VERIFIED",
        account,
        client_ip=client_ip,
        user_agent=user_agent,
        outcome="SUCCESS" if report.permissions_ok else "FAILURE",
        metadata={
            "reachable": report.reachable,
            "permissions_ok": report.permissions_ok,
            "missing_permissions": list(report.missing_permissions),
            "message": report.message,
        },
    )
    await db.commit()
    return verification


async def request_sync(
    db: AsyncSession,
    authorized: Authorized,
    account_id: uuid.UUID,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> SyncAccepted:
    account = await _load(db, account_id)
    try:
        aws_adapter(account.provider)
    except ProviderError as error:
        raise to_app_error(error) from error
    if account.status != "ACTIVE":
        raise to_app_error(NotConfigured(detail_code="account_disabled"))
    # Is a sync running? Try the lock this account's task would hold, and let go at once. A race
    # between two requests is closed by the task itself, which takes the same lock.
    held: bool = (
        await db.execute(
            text("SELECT pg_try_advisory_lock(:ns, hashtext(:id))"),
            {"ns": SYNC_LOCK_NAMESPACE, "id": str(account.id)},
        )
    ).scalar_one()
    if not held:
        raise OperationInProgressError
    await db.execute(
        text("SELECT pg_advisory_unlock(:ns, hashtext(:id))"),
        {"ns": SYNC_LOCK_NAMESPACE, "id": str(account.id)},
    )
    _audit(
        db,
        authorized,
        "CLOUD_ACCOUNT_SYNC_REQUESTED",
        account,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={},
    )
    await db.commit()
    task_id = await _enqueue_sync(account.id, authorized.user.id)
    return SyncAccepted(task_id=task_id)


async def _enqueue_sync(account_id: uuid.UUID, requested_by: uuid.UUID) -> str:
    """After the audit row committed. The task takes two ids and nothing else."""

    def publish() -> str:
        from app.workers.celery_app import celery_app

        result = celery_app.send_task(
            SYNC_TASK,
            args=[str(account_id), str(requested_by)],
            queue="integrations",
            retry=False,
        )
        return str(result.id)

    try:
        return await asyncio.to_thread(publish)
    except Exception as error:
        logger.warning("cloud_sync_enqueue_failed", error=type(error).__name__)
        raise to_app_error(NotConfigured(detail_code="queue_unavailable")) from None
