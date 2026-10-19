"""What a channel's ``destination_reference`` may hold, per channel type (``OPEN-13``, ``OPEN-48``).

EMAIL: one plain address (an address is not a secret). SLACK, TEAMS, WEBHOOK: a secret
reference (``env://NAME``) -- **never the URL itself**, which for these products *is* the
credential. A value that looks like a URL or a token is a different error from a merely
malformed one, so the API can tell the administrator where the value belongs without
repeating it.
"""

from __future__ import annotations

from typing import Final

from app.core.secrets import validate_reference
from app.notifications.address import MailboxError, parse_mailbox

CHANNEL_TYPES: Final[tuple[str, ...]] = ("EMAIL", "TEAMS", "SLACK", "WEBHOOK")
CHANNEL_STATUSES: Final[tuple[str, ...]] = ("ACTIVE", "DISABLED")

SECRET_MISPLACED: Final = "credentials_must_be_stored_in_secret_manager"  # noqa: S105
INVALID: Final = "invalid_destination_reference"


class DestinationError(ValueError):
    """``args[0]`` is a closed reason; the offending value is never included."""


def validate_destination(channel_type: str, reference: str) -> str:
    if "://" in reference and not reference.startswith("env://"):
        raise DestinationError(SECRET_MISPLACED)
    try:
        if channel_type == "EMAIL":
            return parse_mailbox(reference)
        if channel_type in CHANNEL_TYPES:
            return validate_reference(reference)
    except (MailboxError, ValueError):
        raise DestinationError(INVALID) from None
    raise DestinationError(INVALID)
