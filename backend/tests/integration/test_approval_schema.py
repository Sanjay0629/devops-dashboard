"""The ``approvals`` table: what the schema enforces whatever the application does (M4.5).

Each test asserts that **the database** refuses the thing, not that the application remembers to
(docs/DATABASE.md §7, ``OPEN-94``). Migration ``0012``'s other two changes -- ``EXPIRED`` on
``operations.approval_status`` and the ``admin:policies`` permission -- are checked at the end.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, text

from tests.integration.test_alert_schema import a_user
from tests.integration.test_constraints import _account, _server
from tests.integration.test_operation_schema import (
    Target,
    _operation,
    refused,
    target,  # noqa: F401  (a fixture)
)

APPROVAL_SQL = text(
    """
    INSERT INTO approvals (id, operation_type, operation_id, requested_by, approved_by, decision,
                           reason, expires_at, decided_at)
    VALUES (:id, :type, :operation, :requester, :decider, :decision, :reason,
            now() + interval '1 hour', CASE WHEN :decided THEN now() END)
    """
)


def _approval(
    connection: Connection,
    operation: uuid.UUID,
    requester: uuid.UUID,
    *,
    decision: str = "PENDING",
    decider: uuid.UUID | None = None,
    operation_type: str = "SERVICE_OPERATION",
    decided: bool | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    """Insert one row. ``decided`` defaults to "exactly when the decision is not PENDING"."""
    approval_id = uuid.uuid4()
    params: dict[str, Any] = {
        "id": approval_id,
        "type": operation_type,
        "operation": operation,
        "requester": requester,
        "decider": decider,
        "decision": decision,
        "reason": reason,
        "decided": (decision != "PENDING") if decided is None else decided,
    }
    connection.execute(APPROVAL_SQL, params)
    return approval_id


@pytest.fixture
def waiting(target: Target) -> tuple[Connection, uuid.UUID, uuid.UUID, uuid.UUID]:  # noqa: F811
    connection, server, service, requester = target
    operation = _operation(connection, server, service, requester, status="AWAITING_APPROVAL")
    return connection, operation, requester, a_user(connection)


Waiting = tuple[Connection, uuid.UUID, uuid.UUID, uuid.UUID]


def test_a_pending_request_is_accepted_with_no_decider(waiting: Waiting) -> None:
    connection, operation, requester, _ = waiting
    _approval(connection, operation, requester)


@pytest.mark.parametrize("decision", ["MAYBE", "approved", "CANCELLED", ""])
def test_a_decision_outside_the_closed_set_is_refused(waiting: Waiting, decision: str) -> None:
    connection, operation, requester, approver = waiting
    with refused(connection):
        _approval(connection, operation, requester, decision=decision, decider=approver)


def test_an_unknown_operation_type_is_refused(waiting: Waiting) -> None:
    connection, operation, requester, _ = waiting
    with refused(connection):
        _approval(connection, operation, requester, operation_type="SERVER")


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED", "EXPIRED"])
def test_a_decision_without_a_decision_time_is_refused(waiting: Waiting, decision: str) -> None:
    connection, operation, requester, approver = waiting
    with refused(connection):
        _approval(
            connection, operation, requester, decision=decision, decider=approver, decided=False
        )


def test_a_pending_request_with_a_decision_time_is_refused(waiting: Waiting) -> None:
    connection, operation, requester, _ = waiting
    with refused(connection):
        _approval(connection, operation, requester, decided=True)


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED"])
def test_a_decided_request_must_name_its_decider(waiting: Waiting, decision: str) -> None:
    connection, operation, requester, _ = waiting
    with refused(connection):
        _approval(connection, operation, requester, decision=decision, decider=None)


def test_an_expired_request_needs_no_decider(waiting: Waiting) -> None:
    connection, operation, requester, _ = waiting
    _approval(connection, operation, requester, decision="EXPIRED")


def test_two_open_requests_for_one_operation_are_refused(waiting: Waiting) -> None:
    connection, operation, requester, _ = waiting
    _approval(connection, operation, requester)
    with refused(connection):
        _approval(connection, operation, requester)


def test_a_new_request_may_follow_a_closed_one_and_two_operations_may_wait(
    waiting: Waiting,
) -> None:
    connection, operation, requester, approver = waiting
    _approval(connection, operation, requester, decision="REJECTED", decider=approver)
    _approval(connection, operation, requester)  # the first is no longer open
    other = _operation(
        connection,
        _server(
            connection,
            _account(connection, "000000000007"),
            resource_id="i-placeholder0000007",
            hostname="seven.internal.example",
        ),
        None,
        requester,
        status="AWAITING_APPROVAL",
    )
    _approval(connection, other, requester)


def test_one_person_cannot_decide_one_operation_twice(waiting: Waiting) -> None:
    connection, operation, requester, approver = waiting
    _approval(connection, operation, requester, decision="APPROVED", decider=approver)
    with refused(connection):
        _approval(connection, operation, requester, decision="REJECTED", decider=approver)


def test_a_policy_in_use_cannot_be_deleted_from_under_an_approval(waiting: Waiting) -> None:
    connection, operation, requester, _ = waiting
    policy = connection.execute(
        text("SELECT id FROM approval_policies WHERE name = 'production-service-operations'")
    ).scalar_one()
    approval = _approval(connection, operation, requester)
    connection.execute(
        text("UPDATE approvals SET approval_policy_id = :policy WHERE id = :id"),
        {"policy": policy, "id": approval},
    )
    with refused(connection):
        connection.execute(text("DELETE FROM approval_policies WHERE id = :id"), {"id": policy})


def test_the_operation_status_expired_is_accepted_and_others_are_not(target: Target) -> None:  # noqa: F811
    connection, server, service, user = target
    operation = _operation(connection, server, service, user, status="AWAITING_APPROVAL")
    connection.execute(
        text("UPDATE operations SET approval_status = 'EXPIRED' WHERE id = :id"), {"id": operation}
    )
    with refused(connection):
        connection.execute(
            text("UPDATE operations SET approval_status = 'AUTO' WHERE id = :id"),
            {"id": operation},
        )


def test_only_the_administrator_role_holds_the_policy_permission(connection: Connection) -> None:
    holders = connection.execute(
        text(
            """
            SELECT r.name FROM roles r
              JOIN role_permissions rp ON rp.role_id = r.id
              JOIN permissions p ON p.id = rp.permission_id
             WHERE p.resource_type = 'admin' AND p.action = 'policies' AND rp.effect = 'ALLOW'
            """
        )
    ).scalars()
    assert list(holders) == ["ADMINISTRATOR"]
