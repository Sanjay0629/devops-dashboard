"""Server-side session store in Redis (docs/SECURITY.md §5, AD-02, OPEN-04).

**OPEN-04 is resolved as the documented default: Redis-only.** There is no
`user_sessions` table. The trade-off is explicit: a flushed or restarted Redis
logs everyone out, which is an inconvenience, not a loss of record — login,
logout and termination are audited in PostgreSQL, so the authentication history
survives independently of the live sessions.

Four properties this module exists to guarantee:

1. **The cookie value is an opaque, high-entropy identifier** — 256 bits from
   ``secrets.token_urlsafe``. It is not a token, it is not signed, and it
   carries no claims, so nothing about the user can be read or forged from it.
2. **Expiry is decided here, never by the client.** Both deadlines are stored
   as absolute instants and compared against the server clock on every lookup.
   The Redis TTL is a backstop for cleanup, not the control: a key that
   outlived its idle deadline is still rejected.
3. **Permissions are never stored.** PRD §4.1 requires a revocation to take
   effect on the *next request*, which is impossible if the session caches what
   the user may do. The record holds a ``user_id`` and nothing else about
   authority.
4. **Sessions are addressable by user**, so disabling a user or an
   administrator terminating a session takes effect immediately.

The Redis key is a SHA-256 of the identifier rather than the identifier itself.
A session id is a bearer credential for the length of its life, and a key dump,
`MONITOR` trace or `--bigkeys` scan should not hand over usable ones.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

#: 32 bytes = 256 bits, comfortably above the ≥128-bit floor in §5.
_ID_BYTES: Final = 32
_SESSION_PREFIX: Final = "dcp:session:"
_USER_INDEX_PREFIX: Final = "dcp:user-sessions:"


def new_session_id() -> str:
    return secrets.token_urlsafe(_ID_BYTES)


def _key(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{_SESSION_PREFIX}{digest}"


def _user_index_key(user_id: uuid.UUID) -> str:
    return f"{_USER_INDEX_PREFIX}{user_id}"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """What the server knows about a live session.

    ``client_ip`` and ``user_agent`` are coarse fingerprint data kept for
    anomaly *logging* only (§5). They are deliberately not used as an
    authentication factor: a mobile network changes IP mid-session, and
    pinning to one would log out legitimate users while barely inconveniencing
    an attacker who has stolen the cookie.
    """

    user_id: uuid.UUID
    issued_at: datetime
    last_seen_at: datetime
    absolute_deadline: datetime
    idle_deadline: datetime
    client_ip: str | None = None
    user_agent: str | None = None

    def is_expired(self, *, at: datetime | None = None) -> bool:
        at = at or _now()
        return at >= self.absolute_deadline or at >= self.idle_deadline

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_id": str(self.user_id),
                "issued_at": self.issued_at.isoformat(),
                "last_seen_at": self.last_seen_at.isoformat(),
                "absolute_deadline": self.absolute_deadline.isoformat(),
                "idle_deadline": self.idle_deadline.isoformat(),
                "client_ip": self.client_ip,
                "user_agent": self.user_agent,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> SessionRecord:
        data: dict[str, Any] = json.loads(raw)
        return cls(
            user_id=uuid.UUID(data["user_id"]),
            issued_at=datetime.fromisoformat(data["issued_at"]),
            last_seen_at=datetime.fromisoformat(data["last_seen_at"]),
            absolute_deadline=datetime.fromisoformat(data["absolute_deadline"]),
            idle_deadline=datetime.fromisoformat(data["idle_deadline"]),
            client_ip=data.get("client_ip"),
            user_agent=data.get("user_agent"),
        )


async def create(
    user_id: uuid.UUID,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, SessionRecord]:
    """Mint a new session. Returns the identifier and the stored record.

    Called on every login, which is what satisfies "rotated on a
    privilege-relevant change" for the login case: a pre-login session id is
    never carried into the authenticated session.
    """
    settings = get_settings()
    now = _now()
    record = SessionRecord(
        user_id=user_id,
        issued_at=now,
        last_seen_at=now,
        absolute_deadline=now + timedelta(seconds=settings.session_absolute_timeout_seconds),
        idle_deadline=now + timedelta(seconds=settings.session_idle_timeout_seconds),
        client_ip=client_ip,
        user_agent=user_agent,
    )
    session_id = new_session_id()
    client = get_redis()
    async with client.pipeline(transaction=True) as pipe:
        pipe.set(
            _key(session_id),
            record.to_json(),
            ex=settings.session_absolute_timeout_seconds,
        )
        pipe.sadd(_user_index_key(user_id), _key(session_id))
        # The index must not outlive the longest possible session it points at,
        # or a user who never logs in again leaks a key for ever.
        pipe.expire(_user_index_key(user_id), settings.session_absolute_timeout_seconds)
        await pipe.execute()
    # No session id in any log line (§5) — only the user it belongs to.
    logger.info("session_created", user_id=str(user_id))
    return session_id, record


async def load(session_id: str) -> SessionRecord | None:
    """Return a live session and slide its idle deadline, or ``None``.

    ``None`` covers every failure mode on purpose — absent, unparseable,
    expired — because the caller's response to all of them is identical
    (``401``). Distinguishing them in the response would tell an attacker
    whether an identifier was ever valid.
    """
    if not session_id:
        return None
    client = get_redis()
    key = _key(session_id)
    raw = await client.get(key)
    if raw is None:
        return None
    try:
        record = SessionRecord.from_json(raw)
    except (ValueError, KeyError, TypeError):
        # A record this process cannot read is not a record it may trust.
        logger.warning("session_record_unreadable")
        await client.delete(key)
        return None

    now = _now()
    if record.is_expired(at=now):
        await delete(session_id)
        logger.info("session_expired", user_id=str(record.user_id))
        return None

    settings = get_settings()
    # Slide the idle deadline, but never past the absolute one.
    slid = min(
        now + timedelta(seconds=settings.session_idle_timeout_seconds),
        record.absolute_deadline,
    )
    refreshed = SessionRecord(
        user_id=record.user_id,
        issued_at=record.issued_at,
        last_seen_at=now,
        absolute_deadline=record.absolute_deadline,
        idle_deadline=slid,
        client_ip=record.client_ip,
        user_agent=record.user_agent,
    )
    remaining = int((record.absolute_deadline - now).total_seconds())
    if remaining > 0:
        await client.set(key, refreshed.to_json(), ex=remaining)
    return refreshed


async def delete(session_id: str) -> bool:
    """Destroy one session. Idempotent, so logging out twice succeeds."""
    if not session_id:
        return False
    client = get_redis()
    key = _key(session_id)
    raw = await client.get(key)
    user_id: uuid.UUID | None = None
    if raw is not None:
        try:
            user_id = SessionRecord.from_json(raw).user_id
        except (ValueError, KeyError, TypeError):
            user_id = None
    async with client.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        if user_id is not None:
            pipe.srem(_user_index_key(user_id), key)
        results = await pipe.execute()
    return bool(results[0])


async def terminate_user_sessions(user_id: uuid.UUID) -> int:
    """Destroy every session belonging to one user. Returns how many.

    Backs two mandatory behaviours in §5: an administrator terminating another
    user's sessions, and deactivating a user terminating theirs. The callers
    arrive with the user-administration endpoints (M1.4); the primitive lives
    here so the session store owns its own lifecycle.
    """
    client = get_redis()
    index = _user_index_key(user_id)
    # redis-py types smembers() as "awaitable or not" because the same class
    # serves both modes; on the async client it always awaits.
    keys: set[str] = await client.smembers(index)  # type: ignore[misc]
    if not keys:
        return 0
    async with client.pipeline(transaction=True) as pipe:
        pipe.delete(*keys)
        pipe.delete(index)
        results = await pipe.execute()
    removed = int(results[0])
    logger.info("sessions_terminated", user_id=str(user_id), count=removed)
    return removed


async def rotate(
    session_id: str,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, SessionRecord] | None:
    """Replace a live session's identifier, keeping the user signed in.

    For privilege-relevant changes *other* than login (§5): the user's
    authority changed, so the identifier that was issued under the old
    authority is retired. Deadlines restart, which is the conservative
    direction. Returns ``None`` if the session was not live, so a caller
    cannot rotate a dead session into a live one.
    """
    record = await load(session_id)
    if record is None:
        return None
    await delete(session_id)
    return await create(
        record.user_id,
        client_ip=client_ip if client_ip is not None else record.client_ip,
        user_agent=user_agent if user_agent is not None else record.user_agent,
    )
