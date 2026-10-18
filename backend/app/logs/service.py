"""Log search (docs/API_CONTRACTS.md §19.1, AD-05): scope, query, merge, page, redact.

The order of the pipeline is the security argument, so it is written out:

1. **Bounds** (range, retention) are checked before anything else is touched.
2. **Scope is resolved in SQL** (``readable_servers``, §20) into a concrete set of
   server ids *and* what the caller may read on each: application sources need
   ``logs:read:application`` there, system sources ``logs:read:system``. Requested
   ``server_ids`` / ``environment`` / ``application_id`` only narrow that set; none
   of them ever widens it. Servers the caller cannot read are not fetched, not
   counted (except an explicitly requested id, ``excluded_by_scope_count``) and
   not named.
3. The set becomes **at most three selector groups** (application-only,
   system-only, both), each queried with its own source-type restriction, so a
   caller never receives a source type they may not read even when their servers
   differ. Results are merged here by timestamp so one cursor stays exact.
4. Every returned entry is **re-checked** against the scope (a line whose
   ``server_id`` is not in the resolved set, or whose source type that server's
   grant does not cover, is dropped -- fail closed), its labels are re-validated
   (``service`` falls back to ``other``, never a shipper-supplied oddity), its
   ``hostname`` comes from the inventory rather than the shipper's label, and its
   text is **redacted before it is cut** to ``MAX_LINE_CHARS`` so a cut cannot
   split a secret into an unredactable half.

**Paging (OPEN-36).** Loki has no offset, and ``end`` is exclusive (measured). The
cursor therefore carries the timestamp of the last line *and how many lines at
that exact timestamp were already delivered*, plus the last line's hash to verify
it. The next page re-reads from ``timestamp + 1`` ns (backward) or ``timestamp``
(forward), over-fetching by that count, and skips them. This relies on Loki
returning ties in a stable order within an identical query -- observed, not
promised -- so a mismatch is ``422 invalid_cursor`` and never a duplicate or a
silent gap. # ponytail: tie order trusted and verified; a total order by hash
would need the whole tie group fetched.

**Empty is not "no errors" (OPEN-34, issue 93).** The first page carries a
per-server ingestion state; the UI refuses to read an empty page as quiet unless
every selected server is ``receiving``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, get_args

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.dependencies import Authorized, ForbiddenError
from app.authz.queries import readable_servers
from app.core.config import get_settings
from app.core.errors import AppError, NotConfiguredError, field_error
from app.core.logging import get_logger
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.core.redaction import redact_text
from app.inventory.models import ApplicationServer, Server
from app.inventory.schemas import Environment
from app.logs import guards
from app.logs.client import LokiClient, LokiUnavailableError, RawEntry
from app.logs.logql import LogFilter, ingestion_query, log_query
from app.logs.presets import PRESETS
from app.logs.schemas import (
    DEFAULT_LIMIT,
    MAX_CONTAINS,
    MAX_LIMIT,
    MAX_SERVER_IDS,
    SERVICE_NAME,
    SYSTEM_SOURCE_TYPES,
    IngestionState,
    LabelLevel,
    LogEntry,
    LogLimits,
    LogPage,
    LogQueryRequest,
    LogSearchResponse,
    LogSources,
    PresetInfo,
    QueryStats,
    ServerIngestion,
    SourceType,
)

logger = get_logger(__name__)

#: A returned line is cut here, after redaction. Loki's own limit is larger.
MAX_LINE_CHARS: Final = 8_192
#: ``receiving`` / ``quiet`` / ``silent`` boundaries (OPEN-34). Module constants
#: until a deployment asks to tune them (the circuit-breaker precedent).
INGESTION_RECENT_SECONDS: Final = 15 * 60
INGESTION_WINDOW_SECONDS: Final = 24 * 3600
#: Loki's default ``max_entries_limit_per_query``; a page plus its tie-skip
#: overfetch must fit.
LOKI_MAX_ENTRIES: Final = 5_000

_ALL_SOURCES: Final = frozenset(get_args(SourceType))
_LEVELS: Final = frozenset(get_args(LabelLevel))
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class ProviderUnavailableError(AppError):
    status_code = 503
    code = "provider_unavailable"
    message = "Log search is unavailable."


class ProviderTimeoutError(AppError):
    status_code = 504
    code = "provider_timeout"
    message = "Log search timed out."


class NoReadableSourceError(ForbiddenError):
    """The caller holds no permission that covers the requested source type on any
    selected server. A ``403`` (§19.1) and, unlike a scope miss, an audited one."""


def translate(error: LokiUnavailableError) -> AppError:
    """A closed reason becomes a closed HTTP answer. Never an empty result."""
    if error.reason == "not_configured":
        return NotConfiguredError("Log search is not configured.", details={"integration": "loki"})
    if error.reason == "provider_timeout":
        return ProviderTimeoutError(details={"reason": error.reason})
    return ProviderUnavailableError(details={"reason": error.reason})


def make_client() -> LokiClient:
    """The one construction point; tests substitute the transport here."""
    return LokiClient(settings=get_settings())


# --- time ---------------------------------------------------------------------


def to_ns(moment: datetime) -> int:
    delta = moment - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 10**9 + delta.microseconds * 1_000


def from_ns(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value // 1_000)


# --- scope --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Resolved:
    server: Server
    can_application: bool
    can_system: bool

    def source_types(self) -> frozenset[str]:
        allowed: set[str] = set()
        if self.can_application:
            allowed.add("application")
        if self.can_system:
            allowed |= SYSTEM_SOURCE_TYPES
        return frozenset(allowed)


@dataclass(frozen=True, slots=True)
class _Group:
    server_ids: tuple[uuid.UUID, ...]
    #: ``None`` means every source type.
    source_types: tuple[str, ...] | None


async def _resolve(
    db: AsyncSession, authorized: Authorized, body: LogQueryRequest
) -> list[_Resolved]:
    """Servers the caller may read *some* log on, narrowed by the request."""
    can_app = readable_servers(authorized.grants, "logs", "read:application")
    can_sys = readable_servers(authorized.grants, "logs", "read:system")
    statement = (
        select(Server, can_app.label("can_application"), can_sys.label("can_system"))
        .where(Server.deleted_at.is_(None), or_(can_app, can_sys, false()))
        .order_by(Server.display_name, Server.id)
    )
    if body.server_ids is not None:
        statement = statement.where(Server.id.in_(set(body.server_ids)))
    if body.environment is not None:
        statement = statement.where(Server.environment == body.environment)
    if body.application_id is not None:
        statement = statement.where(
            Server.id.in_(
                select(ApplicationServer.server_id).where(
                    ApplicationServer.application_id == body.application_id
                )
            )
        )
    rows = (await db.execute(statement)).all()
    return [_Resolved(row[0], bool(row[1]), bool(row[2])) for row in rows]


def _groups(
    resolved: list[_Resolved], source_type: str | None
) -> tuple[list[_Group], list[_Resolved]]:
    """Group servers by the source types the caller may read on each.

    Returns the groups and the servers dropped because none of the requested
    source types is readable on them.
    """
    buckets: dict[tuple[str, ...] | None, list[uuid.UUID]] = defaultdict(list)
    dropped: list[_Resolved] = []
    for item in resolved:
        types = item.source_types()
        if source_type is not None:
            types = types & {source_type}
        if not types:
            dropped.append(item)
            continue
        key = None if types == _ALL_SOURCES else tuple(sorted(types))
        buckets[key].append(item.server.id)
    return [_Group(tuple(ids), key) for key, ids in buckets.items()], dropped


# --- cursor -------------------------------------------------------------------


def _fingerprint(body: LogQueryRequest, groups: list[_Group]) -> str:
    """Binds a cursor to the filter and the scope it was minted for.

    Stored in the field ``app/core/pagination`` uses to pin the sort, so a cursor
    cannot be replayed against another filter, another direction, or after the
    caller's reach changed (the group membership is part of it). The time range
    and ``limit`` are left out on purpose: a client may legitimately change them
    between pages.
    """
    material = {
        "groups": sorted((sorted(map(str, g.server_ids)), g.source_types) for g in groups),
        "environment": body.environment,
        "application_id": str(body.application_id) if body.application_id else None,
        "service": body.service,
        "level": sorted(body.level) if body.level else None,
        "contains": body.contains,
        "preset": body.pattern_preset,
        "direction": body.direction,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class _Position:
    timestamp_ns: int
    delivered: int
    line_hash: str


def _decode_position(body: LogQueryRequest, fingerprint: str) -> _Position | None:
    if body.cursor is None:
        return None
    cursor = decode_cursor(body.cursor, sort=fingerprint, order=body.direction)
    try:
        count, line_hash = cursor.id.split(":", 1)
        position = _Position(int(cursor.key), int(count), line_hash)
    except ValueError as error:
        raise InvalidCursorError from error
    if position.delivered < 1:
        raise InvalidCursorError
    return position


# --- the query ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Candidate:
    timestamp_ns: int
    raw: RawEntry

    def order(self, sign: int) -> tuple[int, tuple[tuple[str, str], ...], int]:
        """A total order: time, then the stream's labels, then position in the stream.

        Loki returns streams in no promised order, and a page query for a tie group
        is not the same query as the one that cut it. Ordering by the stream's own
        identity makes the order the same however the response was arranged.
        """
        return (sign * self.timestamp_ns, tuple(sorted(self.raw.labels.items())), self.raw.index)


def _entry_hash(server_id: str, source_type: str, service: str, line: str) -> str:
    """Over the **redacted** line: a hash of a secret-bearing line never leaves."""
    return hashlib.sha256(f"{server_id}\0{source_type}\0{service}\0{line}".encode()).hexdigest()[
        :16
    ]


@dataclass(frozen=True, slots=True)
class _Built:
    timestamp_ns: int
    line_hash: str
    entry: LogEntry


def _build(candidate: _Candidate, by_id: dict[str, _Resolved]) -> _Built | None:
    """One stored line to one safe entry, or ``None`` if it must not be shown."""
    labels = candidate.raw.labels
    resolved = by_id.get(labels.get("server_id", ""))
    source_type = labels.get("source_type", "")
    # Fail closed: a line outside the resolved scope, or of a source type the
    # grant on that server does not cover, is dropped, whatever the selector said.
    if resolved is None or source_type not in resolved.source_types():
        return None
    level = labels.get("level", "unknown")
    service = labels.get("service", "other")
    if SERVICE_NAME.fullmatch(service) is None:
        service = "other"
    redacted = redact_text(candidate.raw.line)
    truncated = len(redacted) > MAX_LINE_CHARS
    line = redacted[:MAX_LINE_CHARS] if truncated else redacted
    return _Built(
        candidate.timestamp_ns,
        _entry_hash(str(resolved.server.id), source_type, service, redacted),
        LogEntry(
            timestamp=from_ns(candidate.timestamp_ns),
            server_id=resolved.server.id,
            hostname=resolved.server.hostname,
            service=service,
            source_type=source_type,  # type: ignore[arg-type]  # checked against the grant above
            level=level if level in _LEVELS else "unknown",  # type: ignore[arg-type]
            line=line,
            line_truncated=truncated,
        ),
    )


def _failure(outcomes: list[object]) -> BaseException | None:
    """The failure to report, preferring the reason the circuit opened over a symptom of it."""
    failures = [o for o in outcomes if isinstance(o, BaseException)]
    real = [
        f
        for f in failures
        if not (isinstance(f, LokiUnavailableError) and f.reason == "circuit_open")
    ]
    chosen = real or failures
    return chosen[0] if chosen else None


def _raise(failure: BaseException) -> None:
    if isinstance(failure, LokiUnavailableError):
        raise translate(failure) from failure
    raise failure


async def _fetch(
    loki: LokiClient,
    queries: list[str],
    *,
    start_ns: int,
    end_ns: int,
    limit: int,
    direction: str,
) -> list[tuple[tuple[RawEntry, ...], int | None]]:
    """One bounded query per selector group, with the boundary tie group completed.

    A query cut at ``limit`` may keep a different subset of the lines sharing its
    last timestamp than the merge order here would, and Loki promises nothing about
    which. So a group that came back full has its **last timestamp re-read in
    full** (one more bounded query for exactly that nanosecond) and the partial
    set is replaced. After that the merged top of the page is exact whatever order
    Loki cut in -- the property the paging tests assert with a tie group that
    straddles every page size.
    """
    outcomes = await asyncio.gather(
        *(
            loki.query_range(q, start_ns=start_ns, end_ns=end_ns, limit=limit, direction=direction)
            for q in queries
        ),
        return_exceptions=True,
    )
    if (failure := _failure(list(outcomes))) is not None:
        _raise(failure)
    ranges = [o for o in outcomes if not isinstance(o, BaseException)]

    tails: dict[int, int] = {}
    for gi, result in enumerate(ranges):
        if len(result.entries) >= limit:
            stamps = [e.timestamp_ns for e in result.entries]
            tails[gi] = min(stamps) if direction == "backward" else max(stamps)
    completions = await asyncio.gather(
        *(
            loki.query_range(
                queries[gi],
                start_ns=tail,
                end_ns=tail + 1,
                limit=LOKI_MAX_ENTRIES,
                direction=direction,
            )
            for gi, tail in tails.items()
        ),
        return_exceptions=True,
    )
    if (failure := _failure(list(completions))) is not None:
        _raise(failure)

    merged: list[tuple[tuple[RawEntry, ...], int | None]] = []
    full = dict(zip(tails, completions, strict=True))
    for gi, result in enumerate(ranges):
        entries = result.entries
        scanned = result.bytes_scanned
        completion = full.get(gi)
        if completion is not None and not isinstance(completion, BaseException):
            if len(completion.entries) >= LOKI_MAX_ENTRIES:
                # More lines in one nanosecond than a query can return: refuse
                # rather than page over a group that cannot be made whole.
                raise ProviderUnavailableError(details={"reason": "tie_group_too_large"})
            tail = tails[gi]
            entries = (*(e for e in entries if e.timestamp_ns != tail), *completion.entries)
            if completion.bytes_scanned is not None:
                scanned = (scanned or 0) + completion.bytes_scanned
        merged.append((entries, scanned))
    return merged


async def _ingestion(
    client: LokiClient, servers: list[Server], now: datetime
) -> list[ServerIngestion]:
    """Per-server shipper liveness (OPEN-34). Any failure is ``unknown``, never ``silent``."""
    ids = [server.id for server in servers]
    recent: dict[str, int] | None
    window: dict[str, int] | None
    try:
        recent, window = await asyncio.gather(
            client.counts_by_server(ingestion_query(ids, INGESTION_RECENT_SECONDS), at=now),
            client.counts_by_server(ingestion_query(ids, INGESTION_WINDOW_SECONDS), at=now),
        )
    except LokiUnavailableError as error:
        logger.warning("log_ingestion_unknown", reason=error.reason)
        recent = window = None
    states: list[ServerIngestion] = []
    for server in servers:
        key = str(server.id)
        state: IngestionState
        if recent is None or window is None:
            state = "unknown"
        elif recent.get(key, 0) > 0:
            state = "receiving"
        elif window.get(key, 0) > 0:
            state = "quiet"
        else:
            state = "silent"
        states.append(ServerIngestion(server_id=server.id, hostname=server.hostname, state=state))
    return states


async def search(
    db: AsyncSession,
    authorized: Authorized,
    body: LogQueryRequest,
    *,
    client: LokiClient | None = None,
    now: datetime | None = None,
) -> LogSearchResponse:
    started = time.monotonic()
    settings = get_settings()
    now = now or datetime.now(UTC)
    guards.check_time_range(
        start=body.from_, end=body.to, now=now, retention_days=settings.log_retention_days
    )
    if not settings.loki_configured:
        raise NotConfiguredError("Log search is not configured.", details={"integration": "loki"})

    resolved = await _resolve(db, authorized, body)
    groups, dropped = _groups(resolved, body.source_type)
    if resolved and not groups:
        raise NoReadableSourceError
    searched = [item for item in resolved if item not in dropped]
    guards.check_resolved_servers(len(searched))
    excluded = len(set(body.server_ids)) - len(searched) if body.server_ids is not None else 0

    fingerprint = _fingerprint(body, groups)
    position = _decode_position(body, fingerprint)
    skip = position.delivered if position else 0
    per_group_limit = body.limit + 1 + skip
    if per_group_limit > LOKI_MAX_ENTRIES:
        raise InvalidCursorError

    start_ns, end_ns = to_ns(body.from_), to_ns(body.to)
    if position is not None:
        if body.direction == "backward":
            end_ns = min(end_ns, position.timestamp_ns + 1)
        else:
            start_ns = max(start_ns, position.timestamp_ns)

    loki = client or make_client()
    queries = [
        log_query(
            LogFilter(
                server_ids=group.server_ids,
                source_types=group.source_types,
                environment=body.environment,
                application_id=body.application_id,
                service=body.service,
                levels=tuple(body.level) if body.level else None,
                contains=body.contains,
                preset=body.pattern_preset,
            )
        )
        for group in groups
    ]
    raw_entries: list[_Candidate] = []
    bytes_scanned: int | None = None
    if start_ns < end_ns and queries:
        results = await _fetch(
            loki,
            queries,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=per_group_limit,
            direction=body.direction,
        )
        for entries, scanned in results:
            if scanned is not None:
                bytes_scanned = (bytes_scanned or 0) + scanned
            raw_entries.extend(_Candidate(raw.timestamp_ns, raw) for raw in entries)

    sign = -1 if body.direction == "backward" else 1
    raw_entries.sort(key=lambda c: c.order(sign))
    by_id = {str(item.server.id): item for item in searched}
    built = [b for c in raw_entries if (b := _build(c, by_id)) is not None]

    if position is not None:
        head = built[:skip]
        if (
            len(head) < skip
            or any(b.timestamp_ns != position.timestamp_ns for b in head)
            or head[-1].line_hash != position.line_hash
        ):
            raise InvalidCursorError
        built = built[skip:]

    page = built[: body.limit]
    has_more = len(built) > body.limit
    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        trailing = sum(1 for b in page if b.timestamp_ns == last.timestamp_ns)
        if position is not None and last.timestamp_ns == position.timestamp_ns:
            trailing += position.delivered
        next_cursor = encode_cursor(
            key=last.timestamp_ns,
            row_id=f"{trailing}:{last.line_hash}",
            sort=fingerprint,
            order=body.direction,
        )

    ingestion: list[ServerIngestion] = []
    if position is None and searched:
        ingestion = await _ingestion(loki, [item.server for item in searched], now)

    return LogSearchResponse(
        entries=[b.entry for b in page],
        page=LogPage(limit=body.limit, next_cursor=next_cursor, has_more=has_more),
        query_stats=QueryStats(
            duration_ms=int((time.monotonic() - started) * 1000),
            bytes_scanned=bytes_scanned,
            truncated=has_more,
        ),
        excluded_by_scope_count=excluded,
        servers_searched=len(searched),
        ingestion=ingestion,
    )


async def search_unit(
    db: AsyncSession,
    authorized: Authorized,
    server: Server,
    unit: str,
    *,
    start: datetime,
    end: datetime,
    limit: int,
    level: str | None,
    client: LokiClient | None = None,
) -> LogSearchResponse:
    """§16.7: one registered unit's journal lines, through the same engine.

    The unit name comes from a registered ``services`` row, never from the
    caller. A unit whose name the shipper cannot carry as a plain label (an
    instance unit, or one over 63 characters) is stored as ``other``, so it
    cannot be selected by name: refused honestly rather than answered empty.
    """
    if SERVICE_NAME.fullmatch(unit) is None:
        raise field_error(
            ["path", "service_name"],
            "unselectable",
            "This unit's name cannot be used to select its logs; instance and "
            "over-long units are shipped as 'other'.",
        )
    body = LogQueryRequest.model_validate(
        {
            "from": start,
            "to": end,
            "server_ids": [server.id],
            "service": unit,
            "source_type": "journald",
            "level": [level] if level else None,
            "limit": limit,
        }
    )
    return await search(db, authorized, body, client=client)


def sources(authorized: Authorized) -> LogSources:
    """§19.2. Closed values, so this is the catalogue itself, filtered by capability."""
    types: list[SourceType] = []
    if authorized.permits("logs", "read:application"):
        types.append("application")
    if authorized.permits("logs", "read:system"):
        types.extend(t for t in get_args(SourceType) if t in SYSTEM_SOURCE_TYPES)
    return LogSources(
        source_types=types,
        levels=list(get_args(LabelLevel)),
        environments=list(get_args(Environment)),
        presets=[PresetInfo(id=p.id, label=p.label, description=p.description) for p in PRESETS],  # type: ignore[arg-type]
        limits=LogLimits(
            max_range_days=guards.MAX_RANGE.days,
            retention_days=get_settings().log_retention_days,
            max_limit=MAX_LIMIT,
            default_limit=DEFAULT_LIMIT,
            max_contains=MAX_CONTAINS,
            max_server_ids=MAX_SERVER_IDS,
        ),
    )
