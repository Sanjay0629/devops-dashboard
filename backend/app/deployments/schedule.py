"""The worker's timing decisions, as pure functions (``OPEN-136``, ``OPEN-137``).

Every number is a **working default, not a measurement** (CLAUDE.md §9): a single-host Compose
deployment's guess, to be revisited with real Jenkins behaviour. What is fixed is the shape: a poll
backs off when it keeps failing and is capped, one success resets it, and an unknown trigger
outcome is waited on, then looked for, then given up on -- never retried.
"""

from __future__ import annotations

from typing import Final, Literal

#: A healthy poll, and the first retry after a failed one is twice this.
POLL_INTERVAL_SECONDS: Final = 5
BACKOFF_CAP_SECONDS: Final = 60
#: A row the poller has taken is not offered again for this long; a killed worker's lease simply
#: expires and the row is polled again. Nothing re-triggers: the trigger is only reached by a claim.
LEASE_SECONDS: Final = 60
#: Rows one tick takes. Sequential, so a slow Jenkins costs the tick, not the lease.
BATCH: Final = 20
#: A ``QUEUED`` row with no queue id is "the trigger may have been sent". A live worker is given
#: this long to record its answer before anyone looks.
RECONCILE_AFTER_SECONDS: Final = 60
#: After this, a look that finds nothing is final.
RECONCILE_GIVE_UP_SECONDS: Final = 180
RECONCILE_RETRY_SECONDS: Final = 10
#: An unclaimed row older than this is handed to the queue again.
HEALER_AGE_SECONDS: Final = 30
#: The first poll after the queue id is recorded or a build starts.
FIRST_POLL_SECONDS: Final = 2

ReconcileAction = Literal["wait", "look", "give_up"]


def poll_delay(consecutive_failures: int) -> int:
    """Seconds until the next poll: 5 when healthy, doubling per consecutive failure to 60."""
    if consecutive_failures <= 0:
        return POLL_INTERVAL_SECONDS
    # Cap the exponent first: 2**10**6 is a number nobody wants to compute.
    exponent = min(consecutive_failures, 8)
    return int(min(POLL_INTERVAL_SECONDS * 2**exponent, BACKOFF_CAP_SECONDS))


def reconcile_action(*, age_seconds: float, lost: bool, has_slot: bool) -> ReconcileAction:
    """What to do about a row whose trigger outcome is unknown.

    ``lost`` means Jenkins *knew* the queue item and no longer does, so there is nothing to wait
    for. ``has_slot`` is whether the target's template carries a ``deployment_id`` parameter: with
    none, there is nothing DCP's own id could be found by.
    """
    if not lost and age_seconds < RECONCILE_AFTER_SECONDS:
        return "wait"
    return "look" if has_slot else "give_up"


def empty_look_is_final(*, age_seconds: float, lost: bool) -> bool:
    """A look that found no queue item and no build: is that the answer?"""
    return lost or age_seconds >= RECONCILE_GIVE_UP_SECONDS
