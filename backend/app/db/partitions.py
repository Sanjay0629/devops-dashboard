"""Monthly range partitions for ``audit_events`` and ``alert_events``.

Why this needs a maintenance job rather than being purely declarative: an INSERT
whose partition key falls outside every existing partition **fails**. Because
audit writes share the transaction of the privileged change they record, a
missing partition does not merely lose an audit row — it fails the operation. The same
holds for ``alert_events`` (M3.3): a missing partition makes the webhook answer ``5xx``.
So partitions are created ahead of time and the runway is monitored
(docs/DATABASE.md §16 item 7).

Deliberately loud rather than clever: no default partition. A catch-all would
silently absorb out-of-range rows and quietly defeat retention-by-partition-drop,
which is the whole reason for partitioning. Failing is the correct behaviour,
and the runway check is what stops it happening.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

PARENT_TABLE = "audit_events"
#: Every table that is partitioned monthly. The maintenance task extends and monitors
#: each; a table added here is covered by both with no further change.
PARTITIONED_TABLES = ("audit_events", "alert_events")
#: Months of pre-created partitions to keep ahead of now. Four weeks of slack at
#: the end of a month is comfortable for a job that runs daily.
DEFAULT_RUNWAY_MONTHS = 3
#: Below this, the runway monitor raises an alert (``app/alerts/runway.py``).
RUNWAY_WARNING_MONTHS = 2


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def partition_name(month: date, parent: str = PARENT_TABLE) -> str:
    return f"{parent}_{month:%Y_%m}"


def _checked(parent: str) -> str:
    """The parent is interpolated into DDL, so only the known tables are accepted."""
    if parent not in PARTITIONED_TABLES:
        raise ValueError(f"{parent!r} is not a monthly-partitioned table")
    return parent


def create_partition_sql(month: date, parent: str = PARENT_TABLE) -> str:
    """DDL for one month's partition. Idempotent via IF NOT EXISTS."""
    parent = _checked(parent)
    start = _month_start(month)
    end = _next_month(start)
    return (
        f"CREATE TABLE IF NOT EXISTS {partition_name(start, parent)} "
        f"PARTITION OF {parent} "
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )


def months_from(start: date, count: int) -> list[date]:
    months = [_month_start(start)]
    for _ in range(count - 1):
        months.append(_next_month(months[-1]))
    return months


def ensure_partitions(
    connection: Connection,
    *,
    reference: datetime | None = None,
    runway_months: int = DEFAULT_RUNWAY_MONTHS,
    parent: str = PARENT_TABLE,
) -> list[str]:
    """Create the current month's partition and ``runway_months`` ahead.

    Returns the partition names that were created, so the caller can log
    something meaningful instead of "job ran".
    """
    now = reference or datetime.now(UTC)
    created: list[str] = []
    for month in months_from(now.date(), runway_months + 1):
        name = partition_name(month, parent)
        existing = connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{name}"}
        ).scalar()
        if not existing:
            connection.execute(text(create_partition_sql(month, parent)))
            created.append(name)
    return created


def partition_runway_months(
    connection: Connection, *, reference: datetime | None = None, parent: str = PARENT_TABLE
) -> int:
    """How many future months are already provisioned.

    0 means the current month is the last one that exists — the next month's
    first write will fail -- for ``audit_events``, and with it the operation it was
    recording.
    """
    _checked(parent)
    now = reference or datetime.now(UTC)
    months = 0
    candidate = _next_month(_month_start(now.date()))
    while True:
        exists = connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"public.{partition_name(candidate, parent)}"},
        ).scalar()
        if not exists:
            return months
        months += 1
        candidate = _next_month(candidate)
