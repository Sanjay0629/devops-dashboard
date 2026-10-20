"""Cloud-account tasks (M4.3): the §26.6 synchronisation, on the ``integrations`` queue.

The task takes **two ids and nothing else** (SECURITY.md §22): the account, and the person who
asked, so the audit row can say on whose behalf the system actor worked.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.db.session import get_sync_engine
from app.providers import sync
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="dcp.cloud.sync_account", queue="integrations", time_limit=600, soft_time_limit=540
)
def sync_account(account_id: str, requested_by: str) -> dict[str, Any]:
    try:
        account, requester = uuid.UUID(account_id), uuid.UUID(requested_by)
    except (ValueError, AttributeError, TypeError):
        logger.error("cloud_sync_bad_id")
        return {"outcome": "bad_id"}
    return sync.run(get_sync_engine(), account, requester)
