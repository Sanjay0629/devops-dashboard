"""The audit read touches only the partitions its window overlaps (M4.6, ``OPEN-111``).

``audit_events`` is partitioned monthly by ``occurred_at``. The 90-day ceiling on a query is what
keeps a read from scanning every month; this asserts the planner really does skip the others, with
a control that shows the assertion can fail (a window across the whole history reads them all).

A plan is a measurement of one planner on one database, not a promise about production volumes.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from app.audit.query import AuditFilters, audit_query
from app.authz.matcher import Grant
from app.core.window import Window
from app.db.partitions import ensure_partitions

PARTITION = re.compile(r"^audit_events_\d{4}_\d{2}$")
FULL = [Grant("audit", "read", "ALLOW", {"environment": ["*"]})]


def _relations(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        name = node.get("Relation Name")
        if isinstance(name, str) and PARTITION.match(name):
            found.add(name)
        for value in node.values():
            found |= _relations(value)
    elif isinstance(node, list):
        for value in node:
            found |= _relations(value)
    return found


def _scanned(connection: Connection, window: Window) -> set[str]:
    statement = audit_query(FULL, AuditFilters(), window, order="desc", cursor_token=None, limit=50)
    compiled = statement.compile(dialect=connection.dialect)
    plan = connection.exec_driver_sql(
        "EXPLAIN (FORMAT JSON) " + str(compiled), compiled.params
    ).scalar_one()
    return _relations(plan if isinstance(plan, list) else json.loads(plan))


def test_a_bounded_window_reads_only_the_months_it_overlaps(connection: Connection) -> None:
    now = datetime.now(UTC)
    # Six months of history: five past partitions and the current one (and the runway ahead).
    ensure_partitions(connection, reference=now - timedelta(days=150), runway_months=5)
    for months_ago in range(6):
        connection.execute(
            text(
                "INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type,"
                " environment, correlation_id, outcome, sanitized_metadata)"
                " VALUES (:i, :t, 'SYSTEM', 'system:test', 'PLAN_TEST', 'staging', :c,"
                " 'SUCCESS', '{}'::jsonb)"
            ),
            {"i": uuid.uuid4(), "t": now - timedelta(days=30 * months_ago), "c": uuid.uuid4()},
        )

    week = _scanned(connection, Window(start=now - timedelta(days=7), end=now))
    ninety = _scanned(connection, Window(start=now - timedelta(days=90), end=now))
    everything = _scanned(connection, Window(start=now - timedelta(days=170), end=now))

    # A week overlaps at most two calendar months; ninety days at most four.
    assert 1 <= len(week) <= 2, sorted(week)
    assert 3 <= len(ninety) <= 4, sorted(ninety)
    # The control: with the window wide open the planner reads them all, so the assertions above
    # are measuring the window and not a plan that never looked at partitions.
    assert len(everything) >= 6, sorted(everything)
    assert week < ninety < everything
