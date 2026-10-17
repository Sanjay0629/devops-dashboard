"""The systemd normalisation table, case by case (docs/ARCHITECTURE.md §6.1).

The table has one acceptance-critical rule (PRD §4.5): an unreachable host must
not render as "all services inactive". Most of this file is that rule from every
side — a host that is down, quiet, unobserved or unqueryable, against every
state a unit can be in — plus the precedence order that makes it hold.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest

from app.inventory.models import SERVICE_STATES
from app.inventory.schemas import DataState
from app.metrics.schemas import UnavailableReason
from app.metrics.thresholds import STALE_AFTER_SECONDS
from app.services.normalise import (
    STATE_MAP,
    CollectorObservation,
    HostObservation,
    Normalised,
    ServiceState,
    UnitObservation,
    normalise,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=5)
LAST_SEEN = NOW - timedelta(minutes=20)

ALL_UNIT_STATES = tuple(STATE_MAP)


def host(**overrides: Any) -> HostObservation:
    fields: dict[str, Any] = {
        "os_family": "LINUX",
        "monitoring_enabled": True,
        "has_metrics_instance": True,
        "prometheus_failure": None,
        "up": 1.0,
        "scraped_at": FRESH,
        "last_seen_at": FRESH,
    }
    fields.update(overrides)
    return HostObservation(**fields)


def unit(current: str | None = "active", **overrides: Any) -> UnitObservation:
    """The five series the collector emits for a unit, one of them ``1``."""
    states = {} if current is None else {s: 1.0 if s == current else 0.0 for s in ALL_UNIT_STATES}
    fields: dict[str, Any] = {"states": states}
    fields.update(overrides)
    return UnitObservation(**fields)


WORKING = CollectorObservation(success=1.0)


def run(
    h: HostObservation | None = None,
    c: CollectorObservation | None = None,
    u: UnitObservation | None = None,
) -> Normalised:
    return normalise(h or host(), c or WORKING, u or unit(), now=NOW)


# --- the vocabulary is the database's, not a copy that can drift -------------


def test_the_state_set_is_the_one_the_database_allows() -> None:
    assert set(get_args(ServiceState)) == set(SERVICE_STATES)


def test_the_collector_label_map_covers_exactly_the_five_systemd_states() -> None:
    assert set(STATE_MAP) == {"active", "inactive", "failed", "activating", "deactivating"}
    assert set(STATE_MAP.values()) <= set(SERVICE_STATES)


def test_every_result_uses_closed_vocabularies() -> None:
    """Spot-check that what normalise emits is inside the published enums."""
    data_states = set(get_args(DataState))
    reasons = set(get_args(UnavailableReason))
    for result in (
        run(),
        run(h=host(os_family="WINDOWS")),
        run(h=host(up=0.0)),
        run(c=CollectorObservation(None)),
        run(u=unit(None)),
        run(u=unit("active", states={"active": 1.0})),
    ):
        assert result.state in SERVICE_STATES
        assert result.data_state in data_states
        assert result.unavailable_reason is None or result.unavailable_reason in reasons


# --- table rows 1–5: a healthy host, a collecting exporter -------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("active", "ACTIVE"),
        ("inactive", "INACTIVE"),
        ("failed", "FAILED"),
        ("activating", "ACTIVATING"),
        ("deactivating", "DEACTIVATING"),
    ],
)
def test_active_state_maps_to_the_normalised_state(label: str, expected: str) -> None:
    result = run(u=unit(label))
    assert result.state == expected
    assert result.data_state == "ok"
    assert result.unavailable_reason is None
    assert result.server_reachable is True
    assert result.collected_at == FRESH


def test_active_is_active_whatever_the_sub_state_was() -> None:
    """`active/running` and `active/exited` are one observation to this source.

    `sub_state` is not an input at all (OPEN-29): the distinction is lost, and
    the test is that there is no parameter through which to pretend otherwise.
    """
    assert "sub_state" not in UnitObservation.__slots__  # type: ignore[attr-defined]
    assert run(u=unit("active")).state == "ACTIVE"


# --- row 6: the exporter is not reporting this unit --------------------------


@pytest.mark.parametrize("success", [None, 0.0, 2.0, -1.0])
def test_a_collector_that_is_not_succeeding_is_uncollected(success: float | None) -> None:
    """Not enabled (`None`) and failing (`0`) are one state; anything but `1` is not success."""
    result = run(c=CollectorObservation(success), u=unit("failed"))
    assert result.state == "UNCOLLECTED"
    assert result.server_reachable is True, "the host is fine; only the collection is not"
    assert result.collected_at is None


def test_a_unit_missing_from_a_successful_collection_is_uncollected_not_inactive() -> None:
    """Verified against a real systemd: an unknown unit emits no series at all."""
    result = run(u=unit(None))
    assert result.state == "UNCOLLECTED"
    assert result.state != "INACTIVE"
    assert result.unavailable_reason == "no_data"
    assert result.collected_at is None


# --- row 7 and the acceptance criterion: host staleness wins -----------------

HOST_DOWN: list[tuple[str, HostObservation, DataState]] = [
    ("scrape failing, seen before", host(up=0.0, last_seen_at=LAST_SEEN), "stale"),
    ("scrape failing, never seen", host(up=0.0, last_seen_at=None), "unavailable"),
    ("no up series at all", host(up=None, scraped_at=None, last_seen_at=None), "unavailable"),
    (
        "scrape too old to call current",
        host(scraped_at=NOW - timedelta(seconds=STALE_AFTER_SECONDS + 1)),
        "stale",
    ),
    ("up sample without a timestamp", host(scraped_at=None), "unavailable"),
    ("up is neither 0 nor 1", host(up=0.5), "unavailable"),
]


@pytest.mark.parametrize("unit_state", ALL_UNIT_STATES)
@pytest.mark.parametrize(("why", "down", "data_state"), HOST_DOWN, ids=[c[0] for c in HOST_DOWN])
def test_a_host_that_is_not_demonstrably_up_makes_every_unit_unknown(
    why: str, down: HostObservation, data_state: DataState, unit_state: str
) -> None:
    """PRD §4.5, acceptance-critical, from every side.

    Whatever the unit series say — including `inactive`, which is what a dead
    host's last sample may well read — the answer is UNKNOWN, never INACTIVE.
    """
    result = run(h=down, u=unit(unit_state))
    assert result.state == "UNKNOWN"
    assert result.state != "INACTIVE"
    assert result.data_state == data_state
    assert result.started_at is None
    assert result.restart_count is None


def test_a_failed_unit_on_a_dead_host_is_unknown_not_failed() -> None:
    """Host staleness overrides unit state (§6.1): a stale `failed` is not news."""
    assert run(h=host(up=0.0), u=unit("failed")).state == "UNKNOWN"


def test_a_dead_host_reports_when_it_was_last_collected() -> None:
    result = run(h=host(up=0.0, last_seen_at=LAST_SEEN))
    assert result.collected_at == LAST_SEEN
    assert result.server_reachable is False


def test_a_host_with_no_up_series_is_not_called_unreachable() -> None:
    """No series is "never observed", which is not "observed failing"."""
    assert run(h=host(up=None, scraped_at=None, last_seen_at=None)).server_reachable is None


def test_the_staleness_boundary_is_strictly_greater_than() -> None:
    at_bound = host(scraped_at=NOW - timedelta(seconds=STALE_AFTER_SECONDS))
    past_bound = host(scraped_at=NOW - timedelta(seconds=STALE_AFTER_SECONDS + 1))
    assert run(h=at_bound).state == "ACTIVE"
    assert run(h=past_bound).state == "UNKNOWN"


def test_a_scrape_stamped_in_the_future_is_not_stale() -> None:
    """Clock skew must not turn a good host into an unknown one."""
    assert run(h=host(scraped_at=NOW + timedelta(seconds=3))).state == "ACTIVE"


# --- rows 8, and the configuration gates -------------------------------------


@pytest.mark.parametrize("unit_state", [*ALL_UNIT_STATES, None])
def test_a_platform_without_systemd_is_unsupported(unit_state: str | None) -> None:
    result = run(h=host(os_family="WINDOWS", up=0.0), u=unit(unit_state))
    assert result.state == "UNSUPPORTED"
    assert result.data_state == "unsupported"
    assert result.unavailable_reason == "unsupported_os"
    assert result.state not in {"UNKNOWN", "UNCOLLECTED", "FAILED", "INACTIVE"}


def test_monitoring_switched_off_is_not_configured() -> None:
    result = run(h=host(monitoring_enabled=False))
    assert (result.state, result.data_state, result.unavailable_reason) == (
        "UNKNOWN",
        "not_configured",
        "monitoring_disabled",
    )


def test_no_metrics_instance_is_not_configured() -> None:
    result = run(h=host(has_metrics_instance=False, up=None, scraped_at=None))
    assert (result.state, result.data_state, result.unavailable_reason) == (
        "UNKNOWN",
        "not_configured",
        "no_metrics_instance",
    )


@pytest.mark.parametrize(
    ("failure", "data_state"),
    [
        ("prometheus_not_configured", "not_configured"),
        ("provider_timeout", "unavailable"),
        ("provider_unreachable", "unavailable"),
        ("provider_error", "unavailable"),
        ("circuit_open", "unavailable"),
    ],
)
def test_prometheus_that_cannot_be_asked_leaves_every_unit_unknown(
    failure: UnavailableReason, data_state: DataState
) -> None:
    """We know nothing, and say so — never the last answer the evidence no longer backs."""
    result = run(h=host(prometheus_failure=failure), u=unit("failed"))
    assert result.state == "UNKNOWN"
    assert result.data_state == data_state
    assert result.unavailable_reason == failure


# --- the precedence order is the contract ------------------------------------


def test_the_first_rule_that_applies_wins() -> None:
    everything_wrong = host(
        os_family="WINDOWS",
        monitoring_enabled=False,
        has_metrics_instance=False,
        prometheus_failure="provider_timeout",
        up=0.0,
    )
    assert run(h=everything_wrong).state == "UNSUPPORTED"
    no_systemd_removed = everything_wrong.__class__(
        **{**_fields(everything_wrong), "os_family": "LINUX"}
    )
    assert run(h=no_systemd_removed).unavailable_reason == "monitoring_disabled"
    enabled = host(has_metrics_instance=False, prometheus_failure="provider_timeout", up=0.0)
    assert run(h=enabled).unavailable_reason == "no_metrics_instance"
    queried = host(prometheus_failure="provider_timeout", up=0.0)
    assert run(h=queried).unavailable_reason == "provider_timeout"
    # Host down beats a failing collector: the host is the bigger fact.
    assert run(h=host(up=0.0), c=CollectorObservation(0.0)).state == "UNKNOWN"
    # A collector that is not succeeding beats whatever the unit series say.
    assert run(c=CollectorObservation(0.0), u=unit("failed")).state == "UNCOLLECTED"


def _fields(observation: HostObservation) -> dict[str, Any]:
    return {name: getattr(observation, name) for name in observation.__slots__}  # type: ignore[attr-defined]


# --- a malformed unit is not guessed at --------------------------------------

FIVE = dict.fromkeys(ALL_UNIT_STATES, 0.0)

MALFORMED: list[tuple[str, dict[str, float | None]]] = [
    ("two states set", {**FIVE, "active": 1.0, "failed": 1.0}),
    ("no state set", dict(FIVE)),
    ("a sibling missing", {k: v for k, v in {**FIVE, "active": 1.0}.items() if k != "failed"}),
    ("an unknown label", {**FIVE, "active": 1.0, "reloading": 0.0}),
    ("a NaN sibling", {**FIVE, "active": 1.0, "failed": None}),
    ("the current state is NaN", {**FIVE, "active": None}),
    ("a value that is not 0 or 1", {**FIVE, "active": 1.0, "inactive": 0.5}),
    ("a count instead of a flag", {**FIVE, "active": 2.0}),
]


@pytest.mark.parametrize(("why", "states"), MALFORMED, ids=[m[0] for m in MALFORMED])
def test_a_malformed_unit_is_unknown_with_provider_error(
    why: str, states: dict[str, float | None]
) -> None:
    result = run(u=UnitObservation(states=states))
    assert result.state == "UNKNOWN"
    assert result.data_state == "unavailable"
    assert result.unavailable_reason == "provider_error"
    assert result.server_reachable is True, "the host answered; the unit data is what is wrong"


# --- numbers that are unknown stay unknown -----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (0.0, 0), (3.0, 3), (3.9, 3), (-1.0, None), (math.nan, None), (math.inf, None)],
)
def test_restart_count_is_a_count_or_unknown(value: float | None, expected: int | None) -> None:
    assert run(u=unit("active", restart_total=value)).restart_count == expected


def test_a_missing_restart_series_is_unknown_not_zero() -> None:
    """The one that matters: no series says nothing; zero says "never restarted"."""
    assert run(u=unit("active")).restart_count is None
    assert run(u=unit("active", restart_total=0.0)).restart_count == 0


def test_a_start_time_of_zero_is_unknown_not_1970() -> None:
    """Observed on a real systemd: a stopped or failed unit reports 0."""
    assert run(u=unit("failed", start_time_seconds=0.0)).started_at is None
    assert run(u=unit("inactive", start_time_seconds=0.0)).started_at is None


@pytest.mark.parametrize("value", [None, -5.0, math.nan, math.inf])
def test_an_unusable_start_time_is_unknown(value: float | None) -> None:
    assert run(u=unit("active", start_time_seconds=value)).started_at is None


def test_a_real_start_time_becomes_an_aware_datetime() -> None:
    result = run(u=unit("active", start_time_seconds=1_792_222_259.374206))
    assert result.started_at == datetime(2026, 10, 17, 7, 30, 59, 374206, tzinfo=UTC)
    assert result.started_at is not None and result.started_at.tzinfo is not None


def test_a_started_at_is_not_a_last_state_change() -> None:
    """OPEN-29: the verdict has no field that could carry one."""
    assert "last_state_change_at" not in Normalised.__slots__  # type: ignore[attr-defined]
