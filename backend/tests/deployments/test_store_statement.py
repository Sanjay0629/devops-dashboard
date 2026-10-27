"""``transition_statement`` builds the guarded UPDATE and refuses bad moves (``OPEN-134``).

The statement is the one place a deployment's status changes: the API's cancel uses it now and the
worker's claim, run and finish use it in the next commit. It validates the *move* and the
*evidence* first; the schema's CHECKs are the second guard behind each.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.db.base import all_models
from app.deployments import policy, states
from app.deployments.store import poll_statement, transition_statement

all_models()
ID = uuid.uuid4()
PASSING = policy.none_configured()


def _sql(statement: object) -> str:
    compiled = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
    return re.sub(r"::\w+", "", compiled)  # drop the bind-parameter casts


def _params(statement: object) -> dict[str, object]:
    return dict(statement.compile(dialect=postgresql.dialect()).params)  # type: ignore[attr-defined]


def test_the_update_is_guarded_by_the_expected_current_status() -> None:
    sql = _sql(transition_statement(ID, "REQUESTED", "CANCELLED"))
    assert "WHERE deployments.id = %(id_1)s AND deployments.execution_status = " in sql
    assert "RETURNING deployments.id" in sql
    assert _params(transition_statement(ID, "REQUESTED", "CANCELLED"))["execution_status_1"] == (
        "REQUESTED"
    )


def test_a_terminal_move_stamps_completion_and_stops_the_polling() -> None:
    statement = transition_statement(ID, "REQUESTED", "CANCELLED")
    sql = _sql(statement).replace(" ", "")
    assert "completed_at=now()" in sql and "next_poll_at=" in sql
    assert _params(statement)["next_poll_at"] is None
    assert "started_at" not in sql


def test_the_move_to_running_stamps_the_start_and_needs_the_build_number() -> None:
    statement = transition_statement(ID, "QUEUED", "RUNNING", external_execution_id="42")
    sql = _sql(statement).replace(" ", "")
    assert "started_at=now()" in sql and "completed_at" not in sql
    with pytest.raises(ValueError, match="build number"):
        transition_statement(ID, "QUEUED", "RUNNING")
    with pytest.raises(ValueError, match="build number"):
        transition_statement(ID, "QUEUED", "RUNNING", external_execution_id="")


@pytest.mark.parametrize(
    ("current", "new"),
    [
        ("SUCCEEDED", "FAILED"),
        ("SUCCEEDED", "ROLLED_BACK"),  # M5.5 adds this edge, with the thing that uses it
        ("REQUESTED", "SUCCEEDED"),
        ("REQUESTED", "RUNNING"),  # a worker must claim it (QUEUED) first
        ("QUEUED", "REQUESTED"),
        ("RUNNING", "CANCELLED"),  # DCP does not abort a Jenkins build
        ("FAILED", "RUNNING"),
        ("CANCELLED", "REQUESTED"),
    ],
)
def test_an_illegal_move_is_refused_before_any_statement_exists(current: str, new: str) -> None:
    with pytest.raises(states.InvalidTransitionError):
        transition_statement(ID, current, new, external_execution_id="1")


# --- success needs evidence (the machine's half; the schema's CHECK is the other) -------------


def test_a_success_carries_its_health_result_and_the_pipeline_result() -> None:
    statement = transition_statement(
        ID, "RUNNING", "SUCCEEDED", pipeline_result="SUCCESS", health_check_result=PASSING
    )
    values = _params(statement)
    assert values["health_check_result"] == PASSING
    assert "sanitized_result" in _sql(statement)
    assert "completed_at=now()" in _sql(statement).replace(" ", "")


@pytest.mark.parametrize("result", [None, "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT", "success"])
def test_a_success_without_a_pipeline_success_is_refused(result: str | None) -> None:
    with pytest.raises(states.SuccessEvidenceError):
        transition_statement(
            ID, "RUNNING", "SUCCEEDED", pipeline_result=result, health_check_result=PASSING
        )


@pytest.mark.parametrize(
    "health", [None, {}, {"passed": True}, {**PASSING, "passed": False}, {**PASSING, "passed": 1}]
)
def test_a_success_without_a_passing_health_result_is_refused(health: object) -> None:
    with pytest.raises(states.SuccessEvidenceError):
        transition_statement(
            ID,
            "RUNNING",
            "SUCCEEDED",
            pipeline_result="SUCCESS",
            health_check_result=health,  # type: ignore[arg-type]
        )


# --- failure needs a closed reason ------------------------------------------------------------


def test_a_failure_carries_a_closed_reason_and_says_whether_to_reconcile() -> None:
    plain = _params(transition_statement(ID, "RUNNING", "FAILED", failure_reason="PIPELINE_FAILED"))
    assert plain["failure_reason"] == "PIPELINE_FAILED"
    assert plain["param_1"] == {"reconciliation_required": False}
    lost = _params(transition_statement(ID, "QUEUED", "FAILED", failure_reason="QUEUE_ITEM_LOST"))
    assert lost["param_1"] == {"reconciliation_required": True}
    late = _params(
        transition_statement(ID, "RUNNING", "FAILED", failure_reason="DEPLOYMENT_TIMED_OUT")
    )
    assert late["param_1"] == {"reconciliation_required": True}


@pytest.mark.parametrize("reason", [None, "", "HEALTH_CHECKS_FAILED", "TIMED_OUT", "whatever"])
def test_a_failure_without_a_reason_from_the_closed_set_is_refused(reason: str | None) -> None:
    fields = {} if reason is None else {"failure_reason": reason}
    with pytest.raises(ValueError, match="closed set"):
        transition_statement(ID, "RUNNING", "FAILED", **fields)


def test_a_failure_reason_on_anything_but_a_failure_is_refused() -> None:
    with pytest.raises(ValueError, match="FAILED only"):
        transition_statement(ID, "REQUESTED", "CANCELLED", failure_reason="PIPELINE_FAILED")


def test_a_pipeline_result_travels_into_the_sanitized_result() -> None:
    statement = transition_statement(
        ID, "RUNNING", "FAILED", failure_reason="PIPELINE_UNSTABLE", pipeline_result="UNSTABLE"
    )
    assert _params(statement)["param_1"] == {
        "pipeline_result": "UNSTABLE",
        "reconciliation_required": False,
    }


# --- only what a transition may set -----------------------------------------------------------


def test_an_unknown_field_is_refused_so_a_caller_cannot_widen_the_update() -> None:
    for name in ("execution_status", "deadline_at", "requested_by", "target_digest", "id"):
        with pytest.raises(TypeError, match="not a settable"):
            transition_statement(ID, "REQUESTED", "CANCELLED", **{name: "x"})


def test_the_settable_fields_are_exactly_the_documented_ones() -> None:
    from app.deployments.store import _SETTABLE

    expected = {
        "external_execution_id",
        "queue_item_id",
        "commit_sha",
        "failure_reason",
        "approval_status",
    }
    assert expected == _SETTABLE


# --- poll_after_seconds: a move that keeps the row in flight ----------------------------------


def test_a_claim_schedules_the_next_look_and_clears_the_failure_counters() -> None:
    statement = transition_statement(ID, "REQUESTED", "QUEUED", poll_after_seconds=60)
    sql = _sql(statement).replace(" ", "")
    assert "next_poll_at=(now()+" in sql and "poll_attempts=" in sql and "last_poll_error=" in sql
    params = _params(statement)
    assert params["poll_attempts"] == 0 and params["last_poll_error"] is None


def test_a_finished_deployment_cannot_be_scheduled() -> None:
    with pytest.raises(ValueError, match="not polled"):
        transition_statement(ID, "REQUESTED", "CANCELLED", poll_after_seconds=5)


# --- poll_statement: keeps the status ---------------------------------------------------------


def test_a_poll_statement_is_guarded_by_the_status_it_expects_and_never_moves_it() -> None:
    statement = poll_statement(ID, "RUNNING", delay_seconds=5)
    sql = _sql(statement)
    assert "deployments.execution_status = %(execution_status_1)s" in sql
    assert _params(statement)["execution_status_1"] == "RUNNING"
    assigned = sql.replace(" ", "").split("WHERE")[0]
    assert "execution_status=" not in assigned


def test_a_good_poll_resets_the_count_and_a_failed_one_adds_to_it() -> None:
    good = _params(poll_statement(ID, "RUNNING", delay_seconds=5))
    assert good["poll_attempts"] == 0 and good["last_poll_error"] is None
    bad = poll_statement(ID, "RUNNING", delay_seconds=10, error="provider_error", failed=True)
    assert "poll_attempts=(deployments.poll_attempts+" in _sql(bad).replace(" ", "")
    assert _params(bad)["last_poll_error"] == "provider_error"


@pytest.mark.parametrize("current", ["REQUESTED", "AWAITING_APPROVAL", "APPROVED", "SUCCEEDED"])
def test_only_queued_and_running_rows_are_polled(current: str) -> None:
    with pytest.raises(ValueError, match="polled"):
        poll_statement(ID, current, delay_seconds=5)


@pytest.mark.parametrize(
    "error", ["", "Provider Error", "a sentence from jenkins", "x" * 65, "1abc"]
)
def test_a_poll_error_must_be_a_closed_reason_code(error: str) -> None:
    with pytest.raises(ValueError, match="closed reason"):
        poll_statement(ID, "RUNNING", delay_seconds=5, error=error, failed=True)


def test_a_failed_poll_must_say_why() -> None:
    with pytest.raises(ValueError, match="names its reason"):
        poll_statement(ID, "RUNNING", delay_seconds=5, failed=True)


def test_the_queue_item_is_recorded_once_and_only_on_a_queued_row() -> None:
    statement = poll_statement(ID, "QUEUED", delay_seconds=2, queue_item_id=100)
    assert "deployments.queue_item_id IS NULL" in _sql(statement)
    assert _params(statement)["queue_item_id"] == 100
    with pytest.raises(ValueError, match="queued"):
        poll_statement(ID, "RUNNING", delay_seconds=2, queue_item_id=100)


def test_a_poll_can_merge_closed_fields_into_the_result() -> None:
    statement = poll_statement(
        ID, "QUEUED", delay_seconds=10, result={"trigger": "outcome_unknown"}
    )
    assert _params(statement)["param_1"] == {"trigger": "outcome_unknown"}
