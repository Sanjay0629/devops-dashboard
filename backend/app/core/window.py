"""The time window of a history or audit read (docs/API_CONTRACTS.md §6, §23, §24).

One function, because three endpoints state the same rule and a window that disagrees between the
operations history and the audit trail is a bug nobody would notice until it mattered:

* ``to`` omitted means *now*; ``from`` omitted means ``to`` minus the default (7 days);
* ``from`` after ``to`` is ``422``;
* a range over the maximum (90 days) is ``422`` -- never a silent clamp (§5);
* optionally, a range beyond a shorter bound needs **both** ends stated (§24.1: "``from``/``to``
  required beyond 7 days"), so looking at old data is always a deliberate request.

The bound also keeps the audit read cheap: ``audit_events`` is partitioned by ``occurred_at`` and a
bounded window is what lets the planner skip the months outside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.errors import field_error


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime


def resolve_window(
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime,
    default: timedelta = timedelta(days=7),
    maximum: timedelta = timedelta(days=90),
    explicit_beyond: timedelta | None = None,
) -> Window:
    for name, value in (("from", start), ("to", end)):
        if value is not None and value.tzinfo is None:
            raise field_error(
                ["query", name], "timezone_required", "Give the time with a UTC offset (RFC 3339)."
            )
    resolved_end = end if end is not None else now
    resolved_start = start if start is not None else resolved_end - default
    if resolved_start > resolved_end:
        raise field_error(["query", "from"], "from_after_to", "'from' must not be after 'to'.")
    span = resolved_end - resolved_start
    if span > maximum:
        raise field_error(
            ["query", "from"],
            "range_too_large",
            f"The range is limited to {maximum.days} days.",
        )
    if explicit_beyond is not None and span > explicit_beyond and (start is None or end is None):
        raise field_error(
            ["query", "from"],
            "range_requires_from_and_to",
            f"Give both 'from' and 'to' to look beyond {explicit_beyond.days} days.",
        )
    return Window(start=resolved_start, end=resolved_end)
