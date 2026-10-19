"""The operation state machine, pinned without a database (``OPEN-69``).

``EXPECTED`` is written out here from docs/ARCHITECTURE.md §6.2, independently of the table in
``states.py``, so a pair added to the code without a decision shows up as a failing test and a
diff, not as a quiet change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.operations import states

EXPECTED = {
    ("AWAITING_APPROVAL", "APPROVED"),
    ("AWAITING_APPROVAL", "REJECTED"),
    ("AWAITING_APPROVAL", "CANCELLED"),
    ("PENDING", "RUNNING"),
    ("APPROVED", "RUNNING"),
    ("PENDING", "CANCELLED"),
    ("PENDING", "TIMED_OUT"),
    ("APPROVED", "TIMED_OUT"),
    ("RUNNING", "VERIFYING"),
    ("VERIFYING", "SUCCEEDED"),
    ("VERIFYING", "FAILED"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "TIMED_OUT"),
}
EVERY_STATE = (*states.STATUSES, states.VERIFYING)


def test_the_table_is_exactly_the_documented_diagram() -> None:
    legal = {pair for pair in product(EVERY_STATE, EVERY_STATE) if states.can_transition(*pair)}
    assert legal == EXPECTED


@pytest.mark.parametrize(("current", "new"), sorted(EXPECTED))
def test_every_legal_edge_is_accepted(current: str, new: str) -> None:
    states.require_transition(current, new)


@pytest.mark.parametrize(
    ("current", "new"), sorted(set(product(EVERY_STATE, EVERY_STATE)) - EXPECTED)
)
def test_every_other_pair_is_illegal(current: str, new: str) -> None:
    with pytest.raises(states.InvalidTransitionError):
        states.require_transition(current, new)


def test_a_state_is_not_a_transition_to_itself() -> None:
    assert not any(states.can_transition(s, s) for s in EVERY_STATE)


@pytest.mark.parametrize("terminal", sorted(states.TERMINAL))
def test_terminal_states_are_never_left(terminal: str) -> None:
    assert not any(states.can_transition(terminal, other) for other in EVERY_STATE)


def test_the_stored_values_are_exactly_the_prds_nine() -> None:
    assert set(states.STATUSES) == {
        "PENDING",
        "AWAITING_APPROVAL",
        "APPROVED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
        "CANCELLED",
        "REJECTED",
    }
    assert states.VERIFYING not in states.STATUSES


def test_in_flight_is_every_non_terminal_state() -> None:
    assert set(states.IN_FLIGHT) == set(states.STATUSES) - states.TERMINAL


def test_every_non_terminal_state_has_a_way_out_and_every_state_is_reachable() -> None:
    for state in set(EVERY_STATE) - states.TERMINAL:
        assert any(states.can_transition(state, other) for other in EVERY_STATE), state
    entry = {states.PENDING, states.AWAITING_APPROVAL}
    reached = set(entry)
    frontier = set(entry)
    while frontier:
        frontier = {
            new for old in frontier for new in EVERY_STATE if states.can_transition(old, new)
        } - reached
        reached |= frontier
    assert reached == set(EVERY_STATE)


def test_verifying_is_reported_as_running_and_stored_as_running() -> None:
    assert states.public_status(states.VERIFYING) == states.RUNNING
    assert states.stored_status(states.VERIFYING) == states.RUNNING
    for state in states.STATUSES:
        assert states.public_status(state) == state == states.stored_status(state)


def test_cancellation_is_possible_only_before_a_worker_claims() -> None:
    for state in states.CANCELLABLE:
        assert states.can_transition(state, states.CANCELLED)
    assert not states.can_transition(states.RUNNING, states.CANCELLED)
    assert not states.can_transition(states.APPROVED, states.CANCELLED)


def test_only_the_reaper_edges_reach_timed_out_without_a_claim() -> None:
    assert {s for s in EVERY_STATE if states.can_transition(s, states.TIMED_OUT)} == {
        states.PENDING,
        states.APPROVED,
        states.RUNNING,
    }


# --- verification evidence --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "after", "ok"),
    [
        ("start", "ACTIVE", True),
        ("restart", "ACTIVE", True),
        ("stop", "INACTIVE", True),
        ("start", "INACTIVE", False),
        ("restart", "FAILED", False),
        ("stop", "ACTIVE", False),
        ("stop", "ACTIVATING", False),
        ("start", None, False),
        ("status", "ACTIVE", False),  # not a requestable action: nothing verifies it
        ("logs_tail", "ACTIVE", False),
        ("rm -rf", "ACTIVE", False),
    ],
)
def test_what_counts_as_verified(action: str, after: str | None, ok: bool) -> None:
    assert states.verifies(action, after) is ok


def test_a_success_needs_evidence_that_matches_the_action() -> None:
    states.require_verified_success(states.SUCCEEDED, "restart", "ACTIVE")
    for action, after in [("restart", None), ("restart", "FAILED"), (None, "ACTIVE")]:
        with pytest.raises(states.VerificationRequiredError):
            states.require_verified_success(states.SUCCEEDED, action, after)


def test_the_evidence_rule_only_binds_a_success() -> None:
    for new in (states.FAILED, states.TIMED_OUT, states.CANCELLED):
        states.require_verified_success(new, None, None)


# --- deadline arithmetic ----------------------------------------------------------------------


def test_the_deadline_is_creation_plus_the_policy_timeout() -> None:
    created = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
    assert states.deadline_at(created, 300) == created + timedelta(seconds=300)
    assert states.deadline_at(created, 10) == created + timedelta(seconds=10)
    assert states.deadline_at(created, 3600) == created + timedelta(hours=1)


@pytest.mark.parametrize("timeout", [-1, 0, 9, 3601, 86_400])
def test_a_timeout_outside_the_policy_range_fails_closed(timeout: int) -> None:
    with pytest.raises(ValueError, match="policy range"):
        states.deadline_at(datetime(2026, 1, 15, tzinfo=UTC), timeout)


def test_a_naive_creation_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        states.deadline_at(datetime(2026, 1, 15), 300)  # noqa: DTZ001 -- the point of the test
