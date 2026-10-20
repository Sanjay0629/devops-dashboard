"""§26.6 inventory synchronisation: reconcile, never discover (``OPEN-88``).

For an account's *registered* servers it asks SSM what it knows and records descriptive facts. It
**never** registers a server, and never changes ``environment``, ``status``, ``management_method``
or the account: environment is the authorisation boundary and a provider tag does not set it.
Instances SSM manages that DCP has no server for are *counted* (a number in the audit row), not
created. A value a person entered (``os_version``, ``private_ip``) is filled in only when it was
empty; what the provider reported is kept beside it in ``provider_metadata.ssm`` (stable facts
only -- a ping status that flips would make every edit form's ``If-Unmodified-Since`` stale).
"""

from __future__ import annotations

import ipaddress
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.logging import get_logger
from app.inventory.models import CloudAccount, Server
from app.providers.accounts import SYNC_LOCK_NAMESPACE, aws_adapter
from app.providers.aws import accounts as aws_accounts
from app.providers.aws.api import Target, target_from
from app.providers.errors import ProviderError

logger = get_logger(__name__)

ACTOR_LABEL = "system:cloud-sync"


def _apply(server: Server, facts: aws_accounts.InstanceFacts) -> bool:
    """Record what SSM reports. ``True`` if the row changed."""
    changed = False
    if server.os_version is None and facts.os_version is not None:
        server.os_version = facts.os_version
        changed = True
    if server.private_ip is None and facts.private_ip is not None:
        server.private_ip = ipaddress.IPv4Address(facts.private_ip)
        changed = True
    reported = {
        k: v
        for k, v in (("platform_type", facts.platform_type), ("agent_version", facts.agent_version))
        if v
    }
    metadata: dict[str, Any] = dict(server.provider_metadata or {})
    if reported and metadata.get("ssm") != reported:
        metadata["ssm"] = reported
        server.provider_metadata = metadata
        changed = True
    return changed


def run(engine: Engine, account_id: uuid.UUID, requested_by: uuid.UUID) -> dict[str, Any]:
    """Carry out one synchronisation. Returns the counts, which are also audited."""
    with engine.connect() as lock:
        locked: bool = lock.execute(
            text("SELECT pg_try_advisory_lock(:ns, hashtext(:id))"),
            {"ns": SYNC_LOCK_NAMESPACE, "id": str(account_id)},
        ).scalar_one()
        if not locked:
            return {"outcome": "already_running"}
        try:
            return _run_locked(engine, account_id, requested_by)
        finally:
            lock.execute(
                text("SELECT pg_advisory_unlock(:ns, hashtext(:id))"),
                {"ns": SYNC_LOCK_NAMESPACE, "id": str(account_id)},
            )


def _run_locked(engine: Engine, account_id: uuid.UUID, requested_by: uuid.UUID) -> dict[str, Any]:
    counts = {
        "servers_checked": 0,
        "servers_updated": 0,
        "servers_not_in_ssm": 0,
        "unregistered_managed_instances": 0,
    }
    error_code: str | None = None
    truncated = False
    with Session(engine) as session:
        account = session.get(CloudAccount, account_id)
        if account is None or account.deleted_at is not None or account.status != "ACTIVE":
            return {"outcome": "not_syncable"}
        try:
            api = aws_adapter(account.provider).api
            by_region: dict[str, list[Server]] = defaultdict(list)
            for server in session.execute(
                select(Server).where(
                    Server.cloud_account_id == account.id,
                    Server.deleted_at.is_(None),
                    Server.status != "RETIRED",
                    Server.management_method == "AWS_SSM",
                )
            ).scalars():
                if server.region is not None:
                    by_region[server.region].append(server)
            for region, servers in sorted(by_region.items()):
                target: Target = target_from(
                    role_arn=account.credential_reference,
                    region=region,
                    account_reference=account.account_reference,
                )
                ids = [s.provider_resource_id for s in servers]
                facts = aws_accounts.describe_instances(api, target, ids)
                for server in servers:
                    counts["servers_checked"] += 1
                    found = facts.get(server.provider_resource_id)
                    if found is None:
                        counts["servers_not_in_ssm"] += 1
                    elif _apply(server, found):
                        counts["servers_updated"] += 1
                unregistered, cut = aws_accounts.count_unregistered(api, target, set(ids))
                counts["unregistered_managed_instances"] += unregistered
                truncated = truncated or cut
                session.commit()
        except ProviderError as error:
            error_code = error.detail_code or error.provider_error_class
            session.rollback()
        account = session.get(CloudAccount, account_id)
        if account is not None:
            account.last_sync_at = datetime.now(UTC)
            account.last_sync_error = error_code
            audit.record(
                session,
                action_type="CLOUD_ACCOUNT_SYNCED",
                outcome="SUCCESS" if error_code is None else "FAILURE",
                actor_id=requested_by,
                actor_label=ACTOR_LABEL,
                target_type="cloud_account",
                target_id=account_id,
                metadata={
                    "on_behalf_of": str(requested_by),
                    **counts,
                    "unregistered_count_truncated": truncated,
                    "error": error_code,
                },
            )
            session.commit()
    logger.info("cloud_account_synced", account_id=str(account_id), error=error_code, **counts)
    return {"outcome": "synced" if error_code is None else "failed", "error": error_code, **counts}
