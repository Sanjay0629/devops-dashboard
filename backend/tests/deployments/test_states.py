"""The deployment state machine, pinned without a database (``OPEN-134``).

``EXPECTED`` is written out here from docs/ARCHITECTURE.md §8.1 and ``M5.2_PLAN.md`` D3,
independently of the table in ``states.py``: an edge added to the code without a decision shows up
as a failing test and a diff, not as a quiet change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.deployments import policy, states

EXPECTED = {
    # Approval edges exist for M5.4; no M5.2 code path enters AWAITING_APPROVAL or APPROVED.
    ("AWAITING_APPROVAL", "APPROVED"),
    ("AWAITING_APPROVAL", "CANCELLED"),
    # The worker's claim: committed before the trigger, so it *is* the move to QUEUED.
    ("REQUESTED", "QUEUED"),
    ("APPROVED", "QUEUED"),
    ("REQUESTED", "CANCELLED"),
    # The reaper only: a row nobody claimed before its deadline (added to §8.1 by OPEN-134).
    ("REQUESTED", "FAILED"),
    ("APPROVED", "FAILED"),
    ("QUEUED", "RUNNING"),
    # Jenkins reports the queue item cancelled; DCP does not cancel it.
    ("QUEUED", "CANCELLED"),
    ("QUEUED", "FAILED"),
    ("RUNNING", "SUCCEEDED"),
    ("RUNNING", "FAILED"),
}


def test_the_statuses_are_exactly_the_prd_list() -> None:
    assert set(states.STATUSES) == {
        "REQUESTED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "ROLLED_BACK",
    }
    assert len(states.STATUSES) == 9


def test_the_table_is_exactly_the_documented_diagram() -> None:
    legal = {
        pair for pair in product(states.STATUSES, states.STATUSES) if states.can_transition(*pair)
    }
    assert legal == EXPECTED


@pytest.mark.parametrize(("current", "new"), sorted(EXPECTED))
def test_every_legal_edge_is_accepted(current: str, new: str) -> None:
    states.require_transition(current, new)


@pytest.mark.parametrize(
    ("current", "new"), sorted(set(product(states.STATUSES, states.STATUSES)) - EXPECTED)
)
def test_every_other_pair_is_illegal(current: str, new: str) -> None:
    with pytest.raises(states.InvalidTransitionError):
        states.require_transition(current, new)


def test_an_unknown_status_is_illegal_in_either_position() -> None:
    assert not states.can_transition("RUNNING", "TIMED_OUT")  # no such status (§21)
    assert not states.can_transition("VERIFYING", "SUCCEEDED")  # that is an operations state
    assert not states.can_transition("running", "FAILED")


@pytest.mark.parametrize("status", sorted(states.TERMINAL))
def test_a_terminal_state_has_no_outgoing_edge(status: str) -> None:
    assert not any(states.can_transition(status, other) for other in states.STATUSES)


def test_the_terminal_set_is_the_four_end_states() -> None:
    # SUCCEEDED has no outgoing edge in M5.2; M5.5 adds SUCCEEDED -> ROLLED_BACK with its user.
    assert {"SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"} == states.TERMINAL
    assert states.is_terminal("FAILED") and not states.is_terminal("RUNNING")


def test_nothing_enters_awaiting_approval_and_nothing_enters_rolled_back() -> None:
    assert not any(states.can_transition(s, "AWAITING_APPROVAL") for s in states.STATUSES)
    assert not any(states.can_transition(s, "ROLLED_BACK") for s in states.STATUSES)
    # APPROVED is entered from AWAITING_APPROVAL alone.
    assert {s for s in states.STATUSES if states.can_transition(s, "APPROVED")} == {
        "AWAITING_APPROVAL"
    }


def test_the_claim_is_the_move_to_queued_and_nothing_else_reaches_it() -> None:
    sources = {s for s in states.STATUSES if states.can_transition(s, "QUEUED")}
    assert sources == set(states.CLAIMABLE) == {"REQUESTED", "APPROVED"}


def test_a_build_in_progress_is_reached_only_from_queued() -> None:
    assert {s for s in states.STATUSES if states.can_transition(s, "RUNNING")} == {"QUEUED"}


def test_success_is_reached_only_from_running() -> None:
    assert {s for s in states.STATUSES if states.can_transition(s, "SUCCEEDED")} == {"RUNNING"}


def test_the_in_flight_set_is_every_non_terminal_status() -> None:
    assert set(states.IN_FLIGHT) == set(states.STATUSES) - states.TERMINAL
    assert set(states.IN_FLIGHT) == {
        "REQUESTED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "QUEUED",
        "RUNNING",
    }


def test_a_requester_may_cancel_only_before_a_claim() -> None:
    assert set(states.CANCELLABLE) == {"REQUESTED", "AWAITING_APPROVAL"}
    for status in states.CANCELLABLE:
        assert states.can_transition(status, "CANCELLED")
    # QUEUED -> CANCELLED exists (Jenkins reported it) but is not something a request may ask for.
    assert states.can_transition("QUEUED", "CANCELLED") and "QUEUED" not in states.CANCELLABLE
    assert not states.can_transition("RUNNING", "CANCELLED")


def test_the_poller_looks_only_at_rows_jenkins_may_hold() -> None:
    assert set(states.POLLED) == {"QUEUED", "RUNNING"}


# --- the deadline ----------------------------------------------------------------------------

NOW = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)


def test_the_deadline_is_the_request_time_plus_the_setting() -> None:
    assert states.deadline_at(NOW, 3_600) == NOW + timedelta(hours=1)
    assert states.deadline_at(NOW, states.MIN_DEADLINE_SECONDS) == NOW + timedelta(seconds=60)
    assert states.deadline_at(NOW, states.MAX_DEADLINE_SECONDS) == NOW + timedelta(hours=6)


def test_the_default_is_inside_the_bounds() -> None:
    assert states.MIN_DEADLINE_SECONDS <= states.DEFAULT_DEADLINE_SECONDS
    assert states.DEFAULT_DEADLINE_SECONDS <= states.MAX_DEADLINE_SECONDS
    assert states.DEFAULT_DEADLINE_SECONDS == 3_600


@pytest.mark.parametrize("seconds", [-1, 0, 59, 21_601, 10**9])
def test_a_timeout_outside_the_bounds_fails_closed(seconds: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        states.deadline_at(NOW, seconds)


def test_a_naive_request_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        states.deadline_at(datetime(2026, 1, 15, 10, 0), 3_600)  # noqa: DTZ001 -- the naive case


def test_a_boolean_is_not_a_timeout() -> None:
    with pytest.raises(ValueError):
        states.deadline_at(NOW, True)  # type: ignore[arg-type]


# --- success needs evidence: the machine's half of the two guards ----------------------------


def test_success_requires_a_passing_pipeline_and_a_passing_result() -> None:
    states.require_success("SUCCEEDED", "SUCCESS", policy.none_configured())


@pytest.mark.parametrize("result", [None, "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT", "success"])
def test_success_is_refused_without_a_pipeline_success(result: str | None) -> None:
    with pytest.raises(states.SuccessEvidenceError):
        states.require_success("SUCCEEDED", result, policy.none_configured())


@pytest.mark.parametrize(
    "health",
    [
        None,
        {},
        {"passed": False},
        {"policy": "all_must_pass", "configured": True, "checks": [], "passed": None},
        {"policy": "all_must_pass", "configured": True, "checks": [], "passed": "yes"},
    ],
)
def test_success_is_refused_without_a_passing_health_result(health: object) -> None:
    with pytest.raises(states.SuccessEvidenceError):
        states.require_success("SUCCEEDED", "SUCCESS", health)  # type: ignore[arg-type]


@pytest.mark.parametrize("new", ["FAILED", "CANCELLED", "RUNNING", "QUEUED"])
def test_other_statuses_need_no_success_evidence(new: str) -> None:
    states.require_success(new, None, None)
