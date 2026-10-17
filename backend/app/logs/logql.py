"""Render a validated filter into LogQL (AD-05). Pure; no I/O.

**Nothing from a request is spliced into a query as text.** What reaches this
module is already typed -- UUIDs, members of closed sets, a bounded string -- and
this module checks each one *again* at the point of rendering, because the
guarantee "no raw LogQL" should not rest on every caller having validated. A
violation here raises :class:`ValueError`: it means a programming error upstream,
not a bad request, and it must fail loudly rather than render something.

The one free-form value is ``contains``. It is emitted as a double-quoted LogQL
string with ``\\`` and ``"`` escaped, after control characters are refused. Loki
3.3.2 measured (M3.2 spike): the escaped form matches a hostile string
(``q"uo\\te {x} | ~ `bt` é``) literally, and the same string unescaped is a
``400 parse error`` -- so the escape is doing the work, not luck. ``contains`` is
case-sensitive (``|=``).

The *scope* arrives as ``server_ids``: concrete ids the caller may read,
resolved in SQL by the service. An ``environment`` or ``application_id`` in the
filter only narrows further; it is never the thing that grants reach.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, get_args

from app.inventory.schemas import Environment
from app.logs import guards
from app.logs.presets import BY_ID
from app.logs.schemas import SERVICE_NAME, LabelLevel, SourceType

_SOURCE_TYPES: Final = frozenset(get_args(SourceType))
_LEVELS: Final = frozenset(get_args(LabelLevel))
_ENVIRONMENTS: Final = frozenset(get_args(Environment))


@dataclass(frozen=True, slots=True)
class LogFilter:
    """Everything one Loki query needs, already scoped."""

    server_ids: tuple[uuid.UUID, ...]
    #: ``None`` means every source type; a tuple restricts to exactly those.
    source_types: tuple[str, ...] | None = None
    environment: str | None = None
    application_id: uuid.UUID | None = None
    service: str | None = None
    levels: tuple[str, ...] | None = None
    contains: str | None = None
    preset: str | None = None


def quote(value: str) -> str:
    """A LogQL double-quoted string literal for ``value``."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError("control characters are never rendered into a query")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _matcher(name: str, values: Iterable[str]) -> str:
    """``name="a"`` for one value, ``name=~"a|b"`` for several.

    Every value reaching here is a UUID or a member of a closed set, so none
    contains a regex metacharacter other than ``-`` and ``.``; the ``re.escape``
    in the caller's path would be noise. Asserted by the membership checks.
    """
    items = list(values)
    if len(items) == 1:
        return f"{name}={quote(items[0])}"
    return f"{name}=~{quote('|'.join(items))}"


def _checked(values: Iterable[str], allowed: frozenset[str], what: str) -> list[str]:
    items = list(dict.fromkeys(values))
    if not items:
        raise ValueError(f"{what} must not be empty")
    unknown = [v for v in items if v not in allowed]
    if unknown:
        raise ValueError(f"{what} outside the closed set")
    return items


def stream_selector(f: LogFilter) -> str:
    """The ``{...}`` part. Always carries a positive ``server_id`` matcher."""
    if not f.server_ids:
        raise ValueError("a query needs at least one server")
    if len(f.server_ids) > guards.MAX_RESOLVED_SERVERS:
        raise ValueError("too many servers for one selector")
    ids = list(dict.fromkeys(str(server_id) for server_id in f.server_ids))
    for value in ids:
        uuid.UUID(value)  # raises ValueError on anything but a UUID
    parts = [_matcher("server_id", ids)]
    if f.environment is not None:
        parts.append(
            _matcher("environment", _checked([f.environment], _ENVIRONMENTS, "environment"))
        )
    if f.application_id is not None:
        parts.append(_matcher("application_id", [str(f.application_id)]))
    if f.service is not None:
        if SERVICE_NAME.fullmatch(f.service) is None:
            raise ValueError("service outside the label shape")
        parts.append(_matcher("service", [f.service]))
    if f.source_types is not None:
        parts.append(
            _matcher("source_type", _checked(f.source_types, _SOURCE_TYPES, "source_type"))
        )
    if f.levels is not None:
        parts.append(_matcher("level", _checked(f.levels, _LEVELS, "level")))
    return "{" + ", ".join(parts) + "}"


def log_query(f: LogFilter) -> str:
    """A log query: selector, then ``contains``, then the preset."""
    query = stream_selector(f)
    if f.contains is not None:
        query += f" |= {quote(f.contains)}"
    if f.preset is not None:
        preset = BY_ID.get(f.preset)
        if preset is None:
            raise ValueError("unknown preset")
        query += f" |~ {quote(preset.regex)}"
    return query


def ingestion_query(server_ids: Iterable[uuid.UUID], window_seconds: int) -> str:
    """Lines received per server over the window (``OPEN-34``).

    A server with none is *absent* from the result, which the caller must read as
    zero for that window -- not as "unknown".
    """
    if not 1 <= window_seconds <= 7 * 24 * 3600:
        raise ValueError("window out of range")
    selector = stream_selector(LogFilter(server_ids=tuple(server_ids)))
    return f"sum by (server_id) (count_over_time({selector}[{int(window_seconds)}s]))"
