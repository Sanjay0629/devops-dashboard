"""The worker's timing decisions as pure functions (``OPEN-136``, ``OPEN-137``).

Every number is a named working default, not a measurement. What is pinned here is the *shape*:
a poll backs off on consecutive failures and is capped, a success resets it, and an unknown trigger
outcome is waited on, then looked for, then given up on -- never retried.
"""

from __future__ import annotations

import pytest

from app.deployments import schedule


def test_a_healthy_poll_is_every_five_seconds() -> None:
    assert schedule.poll_delay(0) == schedule.POLL_INTERVAL_SECONDS == 5


@pytest.mark.parametrize(
    ("failures", "delay"), [(1, 10), (2, 20), (3, 40), (4, 60), (5, 60), (50, 60), (10**6, 60)]
)
def test_consecutive_failures_double_the_delay_up_to_a_cap(failures: int, delay: int) -> None:
    assert schedule.poll_delay(failures) == delay


def test_the_delay_never_decreases_as_failures_mount() -> None:
    delays = [schedule.poll_delay(n) for n in range(0, 30)]
    assert delays == sorted(delays)
    assert max(delays) == schedule.BACKOFF_CAP_SECONDS


@pytest.mark.parametrize("failures", [-1, -100])
def test_a_negative_count_is_treated_as_none(failures: int) -> None:
    assert schedule.poll_delay(failures) == schedule.POLL_INTERVAL_SECONDS


def test_the_lease_outlasts_a_poll_and_the_batch_is_bounded() -> None:
    assert schedule.LEASE_SECONDS >= schedule.BACKOFF_CAP_SECONDS
    assert 1 <= schedule.BATCH <= 100


# --- reconciling an unknown trigger outcome ---------------------------------------------------


def test_a_live_worker_is_given_time_to_record_its_answer() -> None:
    assert schedule.reconcile_action(age_seconds=0, lost=False, has_slot=True) == "wait"
    assert schedule.reconcile_action(age_seconds=59.9, lost=False, has_slot=True) == "wait"
    assert schedule.reconcile_action(age_seconds=59.9, lost=False, has_slot=False) == "wait"


def test_after_the_grace_period_it_looks_when_it_can() -> None:
    assert schedule.reconcile_action(age_seconds=60, lost=False, has_slot=True) == "look"
    assert schedule.reconcile_action(age_seconds=500, lost=False, has_slot=True) == "look"


def test_without_a_deployment_id_slot_there_is_nothing_to_look_for_so_it_gives_up() -> None:
    assert schedule.reconcile_action(age_seconds=60, lost=False, has_slot=False) == "give_up"


def test_a_vanished_queue_item_is_looked_for_at_once_not_waited_on() -> None:
    """Jenkins knew it and forgot it: waiting would add nothing."""
    assert schedule.reconcile_action(age_seconds=1, lost=True, has_slot=True) == "look"
    assert schedule.reconcile_action(age_seconds=1, lost=True, has_slot=False) == "give_up"


@pytest.mark.parametrize(
    ("age", "lost", "final"),
    [
        (0, False, False),
        (60, False, False),
        (179, False, False),
        (180, False, True),
        (10_000, False, True),
        (0, True, True),  # it was in the queue and is not: if no build carries the id, it is gone
    ],
)
def test_an_empty_look_is_final_only_after_the_give_up_age_or_for_a_lost_item(
    age: float, lost: bool, final: bool
) -> None:
    assert schedule.empty_look_is_final(age_seconds=age, lost=lost) is final


def test_the_give_up_age_is_after_the_grace_period() -> None:
    assert schedule.RECONCILE_AFTER_SECONDS < schedule.RECONCILE_GIVE_UP_SECONDS
    assert schedule.RECONCILE_RETRY_SECONDS < schedule.RECONCILE_GIVE_UP_SECONDS


def test_the_healer_waits_longer_than_a_worker_needs_to_claim() -> None:
    assert schedule.HEALER_AGE_SECONDS >= 30
