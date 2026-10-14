"""Opaque signed cursors (docs/API_CONTRACTS.md §5).

§5 requires cursors that are "opaque, signed, and encode the sort key plus a
tiebreaker id — never a raw offset". Each of those words is load-bearing:

* **Not an offset.** An offset re-reads the table from the start on every page
  and silently skips or repeats rows when a concurrent insert shifts the
  window. A keyset cursor carries *where the last page ended*, so the next page
  is a range scan from there and a concurrent insert cannot move it.
* **Tiebreaker.** The sort key alone is not unique — two servers can share a
  ``display_name`` or a ``created_at`` to the microsecond — so the id is
  carried with it and the comparison is lexicographic over the pair. Without
  it, rows sharing a key are dropped at a page boundary.
* **Signed.** The payload goes into a ``WHERE`` clause. It is parameterised, so
  a tampered cursor cannot inject SQL, and the query is scope-filtered either
  way — but an unsigned cursor is still a client-controlled query position
  masquerading as a server-issued token, and §5 asks for a signature. A bad
  one is ``422 invalid_cursor``, never a silent reset to page one.

The cursor also pins the sort field and direction it was minted for. Replaying
a ``display_name`` cursor against a ``created_at`` sort would compare a string
to a timestamp and page through nonsense, so that is rejected as invalid rather
than coerced.

**The signing key is derived from the configured database URL** with a fixed
domain-separation salt, rather than coming from a setting of its own. The
derivation is one-way, so it leaks nothing about the DSN; every instance of a
deployment shares that URL, so cursors work across instances and survive a
restart (a per-process random key would break both); and PRD §11 requires the
application to start without production credentials, which a new mandatory
secret would contradict. The cost, accepted: rotating the database password
invalidates in-flight cursors, and a client sees ``422 invalid_cursor`` on the
next page — which is a defined response and one page re-fetch. If a dedicated
key is ever wanted, it is an additive setting with this as the fallback.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from app.core.config import get_settings
from app.core.errors import FieldValidationError

#: Domain separation, so the derived key cannot collide with any other use of
#: the same secret.
_KEY_SALT: Final[bytes] = b"dcp.pagination.cursor.v1"
_SIGNATURE_BYTES: Final[int] = 16


class InvalidCursorError(FieldValidationError):
    """``422 validation_failed`` with the ``invalid_cursor`` field code (§8)."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "loc": ["query", "cursor"],
                    "code": "invalid_cursor",
                    "message": "The cursor is not valid for this query.",
                }
            ]
        )


@dataclass(frozen=True, slots=True)
class Cursor:
    """Where the previous page ended, for one specific sort."""

    #: The sort column's value on the last row of the previous page, as text.
    #: Text because the payload is JSON and a timestamp is not a JSON type;
    #: the caller converts it back using the column it belongs to.
    key: str
    #: The tiebreaker: that row's id.
    id: str
    #: The sort field and direction this cursor was minted for.
    sort: str
    order: str


def _signing_key() -> bytes:
    return hashlib.blake2b(
        get_settings().database_url.get_secret_value().encode("utf-8"),
        key=_KEY_SALT,
        digest_size=32,
    ).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_cursor(*, key: object, row_id: object, sort: str, order: str) -> str:
    """Mint a cursor for the last row of a page."""
    payload = {
        "k": key.isoformat() if isinstance(key, datetime) else str(key),
        "i": str(row_id),
        "s": sort,
        "o": order,
    }
    # separators: compact and, more importantly, stable — the signature covers
    # these exact bytes, so the encoding must not vary between runs.
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.digest(_signing_key(), body, hashlib.sha256)[:_SIGNATURE_BYTES]
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def decode_cursor(value: str, *, sort: str, order: str) -> Cursor:
    """Verify and parse a cursor, or raise :class:`InvalidCursorError`.

    Every failure mode lands on the same exception on purpose: a client has
    nothing to do differently for "bad signature" than for "truncated", and
    distinguishing them would tell a prober which part of the token to work on.
    """
    try:
        encoded_body, encoded_signature = value.split(".", 1)
        body = _b64decode(encoded_body)
        signature = _b64decode(encoded_signature)
    except (ValueError, TypeError) as error:
        raise InvalidCursorError from error

    expected = hmac.digest(_signing_key(), body, hashlib.sha256)[:_SIGNATURE_BYTES]
    if not hmac.compare_digest(signature, expected):
        raise InvalidCursorError

    try:
        payload: Any = json.loads(body)
        parsed = Cursor(
            key=str(payload["k"]),
            id=str(payload["i"]),
            sort=str(payload["s"]),
            order=str(payload["o"]),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise InvalidCursorError from error

    # A valid signature on a cursor for a different sort is still not usable
    # here: the key would be compared against the wrong column.
    if parsed.sort != sort or parsed.order != order:
        raise InvalidCursorError
    return parsed
