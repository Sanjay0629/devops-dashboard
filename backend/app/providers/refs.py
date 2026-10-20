"""Build a :class:`ServerRef` from stored rows. The only inputs are rows; never a request."""

from __future__ import annotations

from app.inventory.models import CloudAccount, Server
from app.providers.types import ServerRef


def server_ref(server: Server, account: CloudAccount) -> ServerRef:
    """``ValueError`` if a stored value no longer has the shape the adapter will accept."""
    return ServerRef(
        provider_resource_id=server.provider_resource_id,
        account_reference=account.account_reference,
        management_method=server.management_method,
        region=server.region,
        credential_reference=account.credential_reference,
    )
