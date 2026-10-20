"""``transition_statement`` builds the guarded UPDATE and refuses bad moves before any SQL."""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.db.base import all_models
from app.operations import states
from app.operations.store import transition_statement

all_models()
OP = uuid.uuid4()


def _sql(statement: object) -> str:
    compiled = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
    return re.sub(r"::\w+", "", compiled)  # drop the bind-parameter casts


def test_the_update_is_guarded_by_the_expected_current_status() -> None:
    sql = _sql(transition_statement(OP, "PENDING", "RUNNING"))
    assert (
        "WHERE operations.id = %(id_1)s AND operations.execution_status = %(execution_status_1)s"
        in sql
    )
    assert "RETURNING operations.id" in sql
    assert "started_at=now()" in sql.replace(" ", "")


def test_a_terminal_move_stamps_completion_and_a_claim_stamps_the_start() -> None:
    terminal = _sql(
        transition_statement(OP, "RUNNING", "FAILED", failure_reason="PROVIDER_REPORTED_FAILURE")
    )
    assert "completed_at" in terminal and "started_at" not in terminal
    claim = _sql(transition_statement(OP, "APPROVED", "RUNNING"))
    assert "started_at" in claim and "completed_at" not in claim


def test_verifying_is_checked_in_the_machine_but_stored_as_running() -> None:
    statement = transition_statement(
        OP, "VERIFYING", "SUCCEEDED", action="restart", observed_state_after="ACTIVE"
    )
    assert statement.compile(dialect=postgresql.dialect()).params["execution_status_1"] == "RUNNING"


def test_a_move_into_verifying_writes_nothing_so_it_is_refused() -> None:
    with pytest.raises(ValueError, match="never written"):
        transition_statement(OP, "RUNNING", "VERIFYING")


@pytest.mark.parametrize(
    ("current", "new"),
    [
        ("SUCCEEDED", "FAILED"),
        ("PENDING", "SUCCEEDED"),
        ("RUNNING", "CANCELLED"),
        ("FAILED", "RUNNING"),
    ],
)
def test_an_illegal_move_is_refused_before_any_statement_exists(current: str, new: str) -> None:
    with pytest.raises(states.InvalidTransitionError):
        transition_statement(OP, current, new)


@pytest.mark.parametrize(
    ("action", "after"),
    [("restart", None), ("restart", "FAILED"), ("stop", "ACTIVE"), (None, "ACTIVE")],
)
def test_a_success_without_matching_evidence_is_refused(
    action: str | None, after: str | None
) -> None:
    kwargs = {} if after is None else {"observed_state_after": after}
    with pytest.raises(states.VerificationRequiredError):
        transition_statement(OP, "VERIFYING", "SUCCEEDED", action=action, **kwargs)


def test_a_failure_needs_a_reason_that_fits_its_status() -> None:
    for new, reason in [
        ("FAILED", None),
        ("FAILED", "PROVIDER_TIMEOUT"),  # that one is TIMED_OUT
        ("TIMED_OUT", "VERIFICATION_MISMATCH"),  # and that one is FAILED
        ("FAILED", "WHATEVER"),
    ]:
        fields = {} if reason is None else {"failure_reason": reason}
        with pytest.raises(ValueError, match="failure_reason"):
            transition_statement(OP, "RUNNING", new, **fields)


def test_a_failure_reason_is_not_accepted_on_a_move_that_is_not_a_failure() -> None:
    with pytest.raises(ValueError, match="belongs to FAILED"):
        transition_statement(OP, "PENDING", "CANCELLED", failure_reason="DEADLINE_EXCEEDED")


def test_only_the_named_columns_can_be_set() -> None:
    for name in ("execution_status", "requested_by", "environment", "deadline_at", "provider"):
        with pytest.raises(TypeError, match="not a settable"):
            transition_statement(OP, "PENDING", "RUNNING", **{name: "x"})


def test_an_approval_move_sets_the_approval_status_and_restarts_the_deadline_in_one_statement() -> (
    None
):
    approved = _sql(
        transition_statement(
            OP,
            "AWAITING_APPROVAL",
            "APPROVED",
            approval_status="APPROVED",
            deadline_after_seconds=300,
        )
    )
    assert "approval_status=" in approved.replace(" ", "")
    assert "deadline_at=(now()+" in approved.replace(" ", "")
    assert "completed_at" not in approved  # still in flight
    assert "operations.execution_status = %(execution_status_1)s" in approved  # the guard stays


def test_a_cancel_or_rejection_from_awaiting_approval_is_terminal_and_stamps_completion() -> None:
    for new in ("CANCELLED", "REJECTED"):
        statement = _sql(
            transition_statement(
                OP,
                "AWAITING_APPROVAL",
                new,
                approval_status="EXPIRED" if new == "CANCELLED" else "REJECTED",
                result={"reason_code": "x"},
            )
        )
        assert "completed_at" in statement and "approval_status" in statement


def test_an_approval_move_that_is_not_in_the_table_is_still_refused() -> None:
    with pytest.raises(states.InvalidTransitionError):
        transition_statement(OP, "AWAITING_APPROVAL", "RUNNING", approval_status="APPROVED")
    with pytest.raises(states.InvalidTransitionError):
        transition_statement(OP, "APPROVED", "REJECTED", approval_status="REJECTED")
