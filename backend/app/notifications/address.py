"""A single plain email address, or nothing (``OPEN-54``).

The destination of an EMAIL channel and the sender both end up in message headers, so the
parser is strict rather than forgiving: no display name, no list, no quoted local part, no
control character, no whitespace. ``EmailMessage`` refuses a header newline as well; this
is the layer that says *why* before a message is ever built.
"""

from __future__ import annotations

import re
from typing import Final

MAX_LENGTH: Final = 254
_MAILBOX: Final = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)


class MailboxError(ValueError):
    """Not a single plain address. The message never repeats the value."""


def parse_mailbox(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_LENGTH
        or ".." in value
        or value.startswith(".")
        or "." + "@" in value
        or _MAILBOX.fullmatch(value) is None
    ):
        raise MailboxError("not a single plain email address")
    return value
