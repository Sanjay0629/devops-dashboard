"""Wire the partition-runway monitor to alerting (``OPEN-40``; promised by ``db/partitions.py``).

A monthly partition that does not exist is a failed INSERT: for ``audit_events`` it fails the
privileged operation being recorded, for ``alert_events`` it makes the webhook answer
``5xx``. The maintenance task already extends the runway daily; this raises an alert when
it has nevertheless fallen short, and closes it when it recovers.

Two rules, mutually exclusive, severity in the name (``OPEN-41``): ``PartitionRunwayLow``
when fewer than ``RUNWAY_WARNING_MONTHS`` months are provisioned, ``PartitionRunwayExhausted``
when none are. Both are ``DCP_SYSTEM`` alerts about no particular server, so only a grant that
constrains no dimension sees them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy.orm import Session

from app.alerts import store
from app.alerts.catalogue import SYSTEM_RULES
from app.alerts.lifecycle import dedup_key
from app.db.partitions import PARTITIONED_TABLES, RUNWAY_WARNING_MONTHS, partition_runway_months

_SYSTEM_TARGET: Final = "system"
_LOW, _EXHAUSTED = SYSTEM_RULES


def sync_alerts(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Open or close the runway alerts to match reality. Commits nothing."""
    now = now or datetime.now(UTC)
    connection = session.connection()
    runways: dict[str, int] = {}
    for table in PARTITIONED_TABLES:
        months = partition_runway_months(connection, parent=table)
        runways[table] = months
        wanted = None
        if months == 0:
            wanted = _EXHAUSTED
        elif months < RUNWAY_WARNING_MONTHS:
            wanted = _LOW
        for rule in (_LOW, _EXHAUSTED):
            key = dedup_key(
                source="DCP_SYSTEM",
                rule_name=rule.name,
                target=_SYSTEM_TARGET,
                discriminators={"table": table},
            )
            if rule is wanted:
                store.fire(
                    session,
                    source="DCP_SYSTEM",
                    dedup=key,
                    rule_name=rule.name,
                    severity=rule.severity,
                    target=store.UNRESOLVED,
                    summary=(
                        f"Partition runway for {table} is {months} month(s); "
                        "the next month's first write fails without a partition"
                    ),
                    details={"table": table, "runway_months": months},
                    now=now,
                )
            else:
                store.resolve(session, dedup=key, now=now)
    return runways
