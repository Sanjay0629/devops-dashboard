"""Scope and paging for the operations history and the audit trail (M4.6, commit 2).

Two things, as in ``test_scope_filtering.py`` and for the same reason.

**One -- the SQL agrees with the matcher.** SECURITY §4 rule 7 says one function decides; the
``WHERE`` clauses in ``app/authz/queries.py`` are a translation of it, and a translation is a second
implementation unless something holds the two together. Each grid below runs real rows through
both and fails if a row is selected by one and refused by the other. The operations grid includes
operations whose server was **re-labelled** since the request, which is the case that distinguishes
the stored environment (``OPEN-107``, PROGRESS issue 164) from the server's current label.

**Two -- a page boundary neither repeats nor skips a row.** Every row shares a ``created_at`` and
many share a ``completed_at``, because one test transaction has one ``now()``; some have none.
Each sort and direction is walked page by page and compared with an oracle written in Python from
the sort rule, not from the query.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.query import AuditFilters, audit_event_query, audit_query
from app.audit.query import next_cursor as audit_next_cursor
from app.authz.matcher import Grant, Target, permits, permits_environmentless
from app.authz.queries import readable_audit_events, readable_operations
from app.core.window import Window
from app.inventory.models import Server
from app.operations.history import HistoryFilters, history_query
from app.operations.history import next_cursor as history_next_cursor
from app.operations.models import Operation
from app.operations.service import operation_target
from tests.integration.test_alert_schema import a_user
from tests.integration.test_constraints import _account, _server
from tests.integration.test_operation_schema import _operation, _service

FOUR = ["development", "staging", "production", "other"]
APP = "66666666-6666-6666-6666-666666666666"


def _window() -> Window:
    now = datetime.now(UTC)
    return Window(start=now - timedelta(days=1), end=now + timedelta(days=1))


def _ref() -> str:
    return f"{uuid.uuid4().int % 10**12:012d}"


def _session(connection: Connection) -> Session:
    return Session(bind=connection, join_transaction_mode="create_savepoint")


# --- the world: accounts, re-labelled servers, operations ------------------------------------


@dataclass
class World:
    account_a: uuid.UUID
    account_b: uuid.UUID
    servers: dict[str, uuid.UUID]
    operations: dict[str, uuid.UUID]  # name -> operation id


def build_world(connection: Connection) -> World:
    """Five servers across two accounts; operations whose stored environment differs from the
    server's current label in two places (``relabelled_*``)."""
    account_a, account_b = _account(connection, _ref()), _account(connection, _ref())
    user = a_user(connection)

    def server(account: uuid.UUID, environment: str) -> uuid.UUID:
        tag = uuid.uuid4().hex[:12]
        return _server(
            connection,
            account,
            resource_id=f"i-{tag}",
            hostname=f"{tag}.internal.example",
            environment=environment,
        )

    servers = {
        "a_staging": server(account_a, "staging"),
        "a_now_staging": server(account_a, "staging"),  # was production when the op was made
        "b_now_production": server(account_b, "production"),  # was staging when the op was made
        "b_development": server(account_b, "development"),
        "a_production": server(account_a, "production"),
    }
    plan = {
        "staging": ("a_staging", "staging"),
        "relabelled_to_staging": ("a_now_staging", "production"),
        "relabelled_to_production": ("b_now_production", "staging"),
        "development": ("b_development", "development"),
        "production": ("a_production", "production"),
        "other": ("a_staging", "other"),
    }
    operations = {}
    for name, (server_name, stored) in plan.items():
        server_id = servers[server_name]
        operations[name] = _operation(
            connection,
            server_id,
            _service(connection, server_id, f"{name}.service"),
            user,
            status="CANCELLED",
            environment=stored,
        )
    return World(account_a, account_b, servers, operations)


def scopes(world: World) -> dict[str, Any]:
    a, b = str(world.account_a), str(world.account_b)
    s = {name: str(value) for name, value in world.servers.items()}
    return {
        "staging": {"environment": ["staging"]},
        "production": {"environment": ["production"]},
        "star": {"environment": ["*"]},
        "staging_and_production": {"environment": ["staging", "production"]},
        "account_a": {"cloud_account": [a]},
        "account_star": {"cloud_account": ["*"]},
        "staging_in_account_b": {"environment": ["staging"], "cloud_account": [b]},
        "server_star": {"server": ["*"]},
        "one_server": {"server": [s["a_staging"]]},
        "two_servers": {"server": [s["a_now_staging"], s["b_now_production"]]},
        "application": {"application": [APP]},
        "application_star": {"application": ["*"]},
        "four_named_and_wildcards": {
            "environment": FOUR,
            "cloud_account": ["*"],
            "server": ["*"],
            "application": ["*"],
        },
        "four_named_only": {"environment": FOUR},
        "empty": {},
        "unknown_dimension": {"unknown_dimension": ["x"]},
        "bare_string": {"environment": "staging"},
    }


_NAMES = ("a_staging", "a_now_staging", "b_now_production", "b_development", "a_production")
SCOPE_NAMES = list(scopes(World(uuid.uuid4(), uuid.uuid4(), {n: uuid.uuid4() for n in _NAMES}, {})))


def grants_for(resource: str, action: str, scope: Any, effect: str) -> list[Grant]:
    if effect == "ALLOW":
        return [Grant(resource, action, "ALLOW", scope)]
    # A broad ALLOW beside the DENY, so the deny has something to subtract from.
    return [
        Grant(resource, action, "ALLOW", {"environment": FOUR}),
        Grant(resource, action, "DENY", scope),
    ]


# --- one: operations --------------------------------------------------------------------------


@pytest.mark.parametrize("scope_name", SCOPE_NAMES)
@pytest.mark.parametrize("effect", ["ALLOW", "DENY"])
def test_the_operations_sql_selects_exactly_what_the_matcher_allows(
    connection: Connection, scope_name: str, effect: str
) -> None:
    world = build_world(connection)
    grants = grants_for("operation", "read", scopes(world)[scope_name], effect)
    ids = list(world.operations.values())
    with _session(connection) as session:
        expected = set()
        for operation_id in ids:
            operation = session.get(Operation, operation_id)
            assert operation is not None
            server = session.get(Server, operation.target_server_id)
            assert server is not None
            if permits(grants, "operation", "read", operation_target(operation, server)).allowed:
                expected.add(operation_id)
        actual = set(
            session.scalars(
                select(Operation.id)
                .join(Server, Server.id == Operation.target_server_id)
                .where(Operation.id.in_(ids), readable_operations(grants, "operation", "read"))
            )
        )
    assert actual == expected, f"{effect} {scope_name}: SQL and matcher disagree"


def test_a_relabelled_server_does_not_move_an_operation_in_or_out_of_scope(
    connection: Connection,
) -> None:
    """The case the stored environment exists for, with an oracle that does not use the matcher."""
    world = build_world(connection)
    ids = world.operations

    def seen(scope: Any) -> set[str]:
        grants = [Grant("operation", "read", "ALLOW", scope)]
        with _session(connection) as session:
            found = set(
                session.scalars(
                    select(Operation.id)
                    .join(Server, Server.id == Operation.target_server_id)
                    .where(
                        Operation.id.in_(list(ids.values())),
                        readable_operations(grants, "operation", "read"),
                    )
                )
            )
        return {name for name, value in ids.items() if value in found}

    # Staging-only reaches what was staging WHEN REQUESTED, wherever the server is labelled now.
    assert seen({"environment": ["staging"]}) == {"staging", "relabelled_to_production"}
    # Production, named, reaches what was production when requested -- including the one whose
    # server has since been relabelled staging.
    assert seen({"environment": ["production"]}) == {"production", "relabelled_to_staging"}
    # "*" never reaches production, stored or otherwise (rule 4).
    assert seen({"environment": ["*"]}) == {
        "staging",
        "relabelled_to_production",
        "development",
        "other",
    }


def test_the_detail_check_and_the_list_use_the_same_basis(connection: Connection) -> None:
    """Issue 164: ``operation_target`` is the stored environment, not the server's label."""
    world = build_world(connection)
    with _session(connection) as session:
        operation = session.get(Operation, world.operations["relabelled_to_staging"])
        assert operation is not None
        server = session.get(Server, operation.target_server_id)
        assert server is not None and server.environment == "staging"
        target = operation_target(operation, server)
    assert target.environment == "production"
    assert (target.cloud_account, target.server) == (str(server.cloud_account_id), str(server.id))
    staging_only = [Grant("operation", "read", "ALLOW", {"environment": ["staging"]})]
    assert not permits(staging_only, "operation", "read", target).allowed


def test_a_requester_sees_their_own_operations_and_nothing_else_without_a_grant(
    connection: Connection,
) -> None:
    world = build_world(connection)
    me = a_user(connection)
    server_id = world.servers["a_production"]
    mine = _operation(
        connection,
        server_id,
        _service(connection, server_id, "mine.service"),
        me,
        status="CANCELLED",
        environment="production",
    )
    with _session(connection) as session:
        rows = session.execute(
            history_query(
                [],
                me,
                HistoryFilters(),
                _window(),
                sort="created_at",
                order="desc",
                cursor_token=None,
                limit=50,
            )
        ).all()
    assert [row.Operation.id for row in rows] == [mine]  # the world's operations belong to another


def test_requested_by_narrows_and_cannot_widen(connection: Connection) -> None:
    world = build_world(connection)
    stranger = a_user(connection)
    me = a_user(connection)
    server_id = world.servers["a_staging"]
    mine = _operation(
        connection,
        server_id,
        _service(connection, server_id, "m.service"),
        me,
        status="CANCELLED",
    )
    with _session(connection) as session:
        asked_for_a_stranger = session.execute(
            history_query(
                [],
                me,
                HistoryFilters(requested_by=stranger),
                _window(),
                sort="created_at",
                order="desc",
                cursor_token=None,
                limit=50,
            )
        ).all()
        asked_for_me = session.execute(
            history_query(
                [],
                me,
                HistoryFilters(requested_by=me),
                _window(),
                sort="created_at",
                order="desc",
                cursor_token=None,
                limit=50,
            )
        ).all()
    assert asked_for_a_stranger == []
    assert [row.Operation.id for row in asked_for_me] == [mine]


# --- one: audit events ------------------------------------------------------------------------

AUDIT_SQL = text(
    """
    INSERT INTO audit_events (id, occurred_at, actor_type, actor_label, action_type, environment,
                              correlation_id, outcome, sanitized_metadata)
    VALUES (:id, now() - :seconds * interval '1 second', 'SYSTEM', 'system:test', :action, :env,
            :correlation, 'SUCCESS', '{}'::jsonb)
    """
)


def audit_event(
    connection: Connection,
    environment: str | None,
    correlation: uuid.UUID,
    seconds: int = 0,
    action: str = "SCOPE_TEST",
) -> uuid.UUID:
    event_id = uuid.uuid4()
    connection.execute(
        AUDIT_SQL,
        {
            "id": event_id,
            "seconds": seconds,
            "action": action,
            "env": environment,
            "correlation": correlation,
        },
    )
    return event_id


def audit_world(connection: Connection) -> tuple[uuid.UUID, dict[str | None, uuid.UUID]]:
    correlation = uuid.uuid4()
    return correlation, {
        env: audit_event(connection, env, correlation)
        for env in ("development", "staging", "production", "other", None)
    }


def visible_events(connection: Connection, grants: list[Grant], ids: list[uuid.UUID]) -> set[Any]:
    return set(
        connection.execute(
            select(AuditEvent.id).where(AuditEvent.id.in_(ids), readable_audit_events(grants))
        ).scalars()
    )


@pytest.mark.parametrize("scope_name", SCOPE_NAMES)
@pytest.mark.parametrize("effect", ["ALLOW", "DENY"])
def test_the_audit_sql_selects_exactly_what_the_matcher_allows(
    connection: Connection, scope_name: str, effect: str
) -> None:
    world = build_world(connection)
    _, events = audit_world(connection)
    grants = grants_for("audit", "read", scopes(world)[scope_name], effect)

    expected = {
        event_id
        for environment, event_id in events.items()
        if (
            permits_environmentless(grants, "audit", "read")
            if environment is None
            else permits(grants, "audit", "read", Target(environment=environment))
        ).allowed
    }
    assert visible_events(connection, grants, list(events.values())) == expected, (
        f"{effect} {scope_name}: SQL and matcher disagree"
    )


def test_what_each_kind_of_audit_reader_sees(connection: Connection) -> None:
    """The ``OPEN-109`` table, written out, with an oracle that does not use the matcher."""
    _, events = audit_world(connection)
    ids = list(events.values())
    by_name = dict(events)
    everything = set(ids)

    def seen(*grants: Grant) -> set[Any]:
        return visible_events(connection, list(grants), ids)

    def allow(scope: Any) -> Grant:
        return Grant("audit", "read", "ALLOW", scope)

    full = allow(
        {"environment": FOUR, "cloud_account": ["*"], "server": ["*"], "application": ["*"]}
    )
    # The bootstrap administrator: every event, including the one with no environment.
    assert seen(full) == everything
    # A production-only approver: production, and not a login or a role grant.
    assert seen(allow({"environment": ["production"]})) == {by_name["production"]}
    # "*" is not production (rule 4) but does reach an event with no environment.
    assert seen(allow({"environment": ["*"]})) == everything - {by_name["production"]}
    assert seen(allow({"environment": ["staging"]})) == {by_name["staging"]}
    # Three of four environments do not "cover every environment".
    assert seen(allow({"environment": ["development", "staging", "other"]})) == {
        by_name["development"],
        by_name["staging"],
        by_name["other"],
    }
    # A grant on a server, an account or an application reaches no audit row at all.
    server = str(uuid.uuid4())
    assert seen(allow({"server": [server]})) == set()
    assert seen(allow({"cloud_account": [str(uuid.uuid4())]})) == set()
    assert seen(allow({"environment": ["*"], "application": [APP]})) == set()
    # A deny that names no production does not reach production (rule 4 applies to a deny too), but
    # it does remove the no-environment event; a deny on production alone removes production only.
    assert seen(full, Grant("audit", "read", "DENY", {"environment": ["*"]})) == {
        by_name["production"]
    }
    assert seen(full, Grant("audit", "read", "DENY", {"environment": ["production"]})) == (
        everything - {by_name["production"]}
    )
    # No audit grant at all, or a grant for something else: nothing.
    assert seen() == set()
    assert seen(Grant("operation", "read", "ALLOW", full.scope)) == set()


def test_one_event_by_id_is_found_only_inside_scope(connection: Connection) -> None:
    _, events = audit_world(connection)
    production = events["production"]
    reaches = [Grant("audit", "read", "ALLOW", {"environment": ["production"]})]
    does_not = [Grant("audit", "read", "ALLOW", {"environment": ["staging"]})]
    assert connection.execute(audit_event_query(reaches, production)).first() is not None
    assert connection.execute(audit_event_query(does_not, production)).first() is None
    assert connection.execute(audit_event_query(reaches, uuid.uuid4())).first() is None


# --- two: paging ------------------------------------------------------------------------------


def _paged_operations(
    session: Session, me: uuid.UUID, sort: str, order: str, limit: int
) -> list[uuid.UUID]:
    filters = HistoryFilters()
    found: list[uuid.UUID] = []
    token: str | None = None
    for _ in range(50):
        rows = session.execute(
            history_query(
                [],
                me,
                filters,
                _window(),
                sort=sort,
                order=order,
                cursor_token=token,
                limit=limit,
            )
        ).all()
        page = rows[:limit]
        found.extend(row.Operation.id for row in page)
        if len(rows) <= limit:
            return found
        token = history_next_cursor(page[-1].Operation, filters, sort=sort, order=order)
    raise AssertionError("paging did not terminate")


def _operations_with_ties(connection: Connection) -> tuple[uuid.UUID, list[Operation]]:
    """Seven operations: shared and distinct timestamps, finished and unfinished."""
    me = a_user(connection)
    server_id = _server(
        connection,
        _account(connection, _ref()),
        resource_id=f"i-{uuid.uuid4().hex[:12]}",
        hostname=f"{uuid.uuid4().hex[:12]}.internal.example",
    )
    base = datetime.now(UTC).replace(microsecond=0)
    # (created minutes ago, completed minutes ago or None for unfinished)
    shape = [(0, 0), (0, None), (0, 0), (1, None), (1, 1), (2, 1), (3, None)]
    for index, (created, completed) in enumerate(shape):
        operation_id = _operation(
            connection,
            server_id,
            _service(connection, server_id, f"u{index}.service"),
            me,
            status="CANCELLED" if completed is not None else "PENDING",
        )
        connection.execute(
            text("UPDATE operations SET created_at = :c, completed_at = :d WHERE id = :id"),
            {
                "id": operation_id,
                "c": base - timedelta(minutes=created),
                "d": None if completed is None else base - timedelta(minutes=completed),
            },
        )
    with _session(connection) as session:
        rows = list(session.scalars(select(Operation).where(Operation.requested_by == me)))
        session.expunge_all()
    return me, rows


def _oracle(rows: list[Operation], sort: str, order: str) -> list[uuid.UUID]:
    """The sort rule, written from the spec: dated rows by (value, id), NULLs last, both ways."""
    reverse = order == "desc"

    def value(row: Operation) -> datetime | None:
        return row.completed_at if sort == "completed_at" else row.created_at

    dated = sorted((r for r in rows if value(r) is not None), key=lambda r: (value(r), r.id))
    undated = sorted((r for r in rows if value(r) is None), key=lambda r: r.id)
    if reverse:
        dated.reverse()
        undated.reverse()
    return [r.id for r in [*dated, *undated]]


@pytest.mark.parametrize("limit", [1, 2, 3, 7, 50])
@pytest.mark.parametrize("order", ["desc", "asc"])
@pytest.mark.parametrize("sort", ["created_at", "completed_at"])
def test_paging_neither_repeats_nor_skips_a_row_across_ties_and_nulls(
    connection: Connection, sort: str, order: str, limit: int
) -> None:
    me, rows = _operations_with_ties(connection)
    with _session(connection) as session:
        paged = _paged_operations(session, me, sort, order, limit)
    assert len(paged) == len(set(paged)) == len(rows)
    assert paged == _oracle(rows, sort, order)


def test_unfinished_operations_come_last_in_both_directions(connection: Connection) -> None:
    me, rows = _operations_with_ties(connection)
    unfinished = {r.id for r in rows if r.completed_at is None}
    with _session(connection) as session:
        for order in ("asc", "desc"):
            paged = _paged_operations(session, me, "completed_at", order, 50)
            assert set(paged[-len(unfinished) :]) == unfinished


def test_a_window_bounds_created_at_whichever_way_the_page_is_sorted(
    connection: Connection,
) -> None:
    me, rows = _operations_with_ties(connection)
    newest = max(r.created_at for r in rows)
    window = Window(start=newest - timedelta(seconds=30), end=newest + timedelta(days=1))
    expected = {r.id for r in rows if r.created_at >= window.start}
    assert 0 < len(expected) < len(rows)
    for sort in ("created_at", "completed_at"):
        with _session(connection) as session:
            got = session.execute(
                history_query(
                    [],
                    me,
                    HistoryFilters(),
                    window,
                    sort=sort,
                    order="desc",
                    cursor_token=None,
                    limit=50,
                )
            ).all()
        assert {row.Operation.id for row in got} == expected


def test_audit_paging_neither_repeats_nor_skips_across_shared_timestamps(
    connection: Connection,
) -> None:
    correlation = uuid.uuid4()
    # Ties: five events share the transaction's now(); four are older, two of those together.
    ids = [
        audit_event(connection, "staging", correlation, seconds)
        for seconds in (0, 0, 0, 0, 0, 5, 5, 9, 12)
    ]
    full = [
        Grant(
            "audit",
            "read",
            "ALLOW",
            {"environment": FOUR, "cloud_account": ["*"], "server": ["*"], "application": ["*"]},
        )
    ]
    filters = AuditFilters(correlation_id=correlation)
    stored = connection.execute(
        select(AuditEvent.occurred_at, AuditEvent.id).where(AuditEvent.id.in_(ids))
    ).all()
    for order in ("desc", "asc"):
        oracle = [
            row.id
            for row in sorted(stored, key=lambda r: (r.occurred_at, r.id), reverse=order == "desc")
        ]
        for limit in (1, 2, 4, 9, 50):
            found: list[uuid.UUID] = []
            token = None
            with _session(connection) as session:
                for _ in range(50):
                    rows = list(
                        session.scalars(
                            audit_query(
                                full,
                                filters,
                                _window(),
                                order=order,
                                cursor_token=token,
                                limit=limit,
                            )
                        )
                    )
                    page = rows[:limit]
                    found.extend(event.id for event in page)
                    if len(rows) <= limit:
                        break
                    token = audit_next_cursor(page[-1], filters, order=order)
            assert found == oracle, f"{order} limit {limit}"


def test_the_audit_window_excludes_events_outside_it(connection: Connection) -> None:
    correlation = uuid.uuid4()
    inside = audit_event(connection, "staging", correlation, 0)
    old = audit_event(connection, "staging", correlation, 3 * 3600)
    full = [Grant("audit", "read", "ALLOW", {"environment": FOUR})]
    now = datetime.now(UTC)
    window = Window(start=now - timedelta(hours=1), end=now + timedelta(hours=1))
    with _session(connection) as session:
        got = {
            event.id
            for event in session.scalars(
                audit_query(
                    full,
                    AuditFilters(correlation_id=correlation),
                    window,
                    order="desc",
                    cursor_token=None,
                    limit=50,
                )
            )
        }
    assert got == {inside} and old not in got


FILTERS: list[tuple[str, Callable[[uuid.UUID], AuditFilters]]] = [
    ("outcome", lambda _c: AuditFilters(outcome="SUCCESS")),
    ("environment", lambda _c: AuditFilters(environment="staging")),
    ("action", lambda _c: AuditFilters(action_type="SCOPE_TEST")),
    ("actor_type", lambda _c: AuditFilters(actor_type="SYSTEM")),
]


@pytest.mark.parametrize(("name", "make"), FILTERS, ids=[name for name, _ in FILTERS])
def test_an_audit_filter_narrows_and_never_widens_scope(
    connection: Connection, name: str, make: Callable[[uuid.UUID], AuditFilters]
) -> None:
    correlation, events = audit_world(connection)
    production_only = [Grant("audit", "read", "ALLOW", {"environment": ["production"]})]
    filters = replace(make(correlation), correlation_id=correlation)
    with _session(connection) as session:
        got = {
            e.id
            for e in session.scalars(
                audit_query(
                    production_only,
                    filters,
                    _window(),
                    order="desc",
                    cursor_token=None,
                    limit=50,
                )
            )
        }
    # Asking for staging, or for a filter every event matches, still yields only production.
    assert got <= {events["production"]}
    if name == "environment":
        assert got == set()
