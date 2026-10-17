"""The cached view behind the list endpoints (docs/DATABASE.md §4.5).

A list row is a cache aged at read time. The rule these tests hold is the same
one ``test_normalise`` holds for the live path: a host that is not demonstrably
fine makes the row ``UNKNOWN``, whatever the cache last said — and a cache that
has gone quiet is *stale*, not "still the last known good value".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.normalise import (
    CACHE_STALE_AFTER_SECONDS,
    STATE_MAP,
    Normalised,
    view_cached,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)
STALE = NOW - timedelta(seconds=CACHE_STALE_AFTER_SECONDS + 1)


def view(**overrides: Any) -> Normalised:
    fields: dict[str, Any] = {
        "os_family": "LINUX",
        "monitoring_enabled": True,
        "has_metrics_instance": True,
        "agent_status": "HEALTHY",
        "cached_state": "ACTIVE",
        "observed_at": FRESH,
        "now": NOW,
    }
    fields.update(overrides)
    return view_cached(**fields)


@pytest.mark.parametrize("state", sorted(STATE_MAP.values()))
def test_a_fresh_cached_state_is_shown_with_its_collection_time(state: str) -> None:
    result = view(cached_state=state)
    assert (result.state, result.data_state) == (state, "ok")
    assert result.collected_at == FRESH
    assert result.server_reachable is True


@pytest.mark.parametrize("cached", sorted(STATE_MAP.values()))
def test_an_unreachable_host_overrides_every_cached_state(cached: str) -> None:
    """The acceptance criterion, on the list path: never the cached ACTIVE, never INACTIVE."""
    result = view(agent_status="UNREACHABLE", cached_state=cached)
    assert result.state == "UNKNOWN"
    assert result.data_state == "stale"
    assert result.server_reachable is False
    assert result.collected_at == FRESH, "the time it was last true, not now"


def test_an_unreachable_host_that_was_never_observed_is_unknown() -> None:
    result = view(agent_status="UNREACHABLE", observed_at=None)
    assert (result.state, result.data_state, result.server_reachable) == (
        "UNKNOWN",
        "unavailable",
        False,
    )


def test_a_cache_never_written_is_unknown_not_inactive() -> None:
    result = view(cached_state="UNKNOWN", observed_at=None)
    assert (result.state, result.data_state, result.collected_at) == (
        "UNKNOWN",
        "unavailable",
        None,
    )


def test_the_cache_goes_stale_strictly_after_the_bound() -> None:
    at_bound = NOW - timedelta(seconds=CACHE_STALE_AFTER_SECONDS)
    assert view(observed_at=at_bound).state == "ACTIVE"
    assert view(observed_at=STALE).state == "UNKNOWN"
    assert view(observed_at=STALE).data_state == "stale"


@pytest.mark.parametrize("cached", sorted(STATE_MAP.values()))
def test_a_stale_cache_does_not_keep_showing_its_state(cached: str) -> None:
    assert view(observed_at=STALE, cached_state=cached).state == "UNKNOWN"


def test_a_cached_uncollected_stays_uncollected_and_is_not_inactive() -> None:
    result = view(cached_state="UNCOLLECTED")
    assert (result.state, result.data_state) == ("UNCOLLECTED", "unavailable")


def test_a_cached_unknown_stays_unknown() -> None:
    assert view(cached_state="UNKNOWN").state == "UNKNOWN"


def test_a_cached_unsupported_on_a_linux_host_is_not_believed() -> None:
    """The gates would not have produced it, so the row is not trusted to say it."""
    assert view(cached_state="UNSUPPORTED").state == "UNKNOWN"


def test_an_unrecognised_cached_state_fails_closed() -> None:
    assert view(cached_state="REBOOTING").state == "UNKNOWN"


@pytest.mark.parametrize("cached", [*sorted(STATE_MAP.values()), "UNKNOWN", "UNCOLLECTED"])
def test_a_host_without_systemd_is_unsupported_whatever_was_cached(cached: str) -> None:
    result = view(os_family="WINDOWS", cached_state=cached, agent_status="UNREACHABLE")
    assert (result.state, result.data_state) == ("UNSUPPORTED", "unsupported")


def test_the_configuration_gates_come_before_the_cache() -> None:
    assert view(monitoring_enabled=False).unavailable_reason == "monitoring_disabled"
    assert view(has_metrics_instance=False).unavailable_reason == "no_metrics_instance"
    assert view(monitoring_enabled=False, cached_state="FAILED").state == "UNKNOWN"


@pytest.mark.parametrize(("agent_status", "reachable"), [("HEALTHY", True), ("UNKNOWN", None)])
def test_reachability_is_only_claimed_when_the_probe_said_so(
    agent_status: str, reachable: bool | None
) -> None:
    assert view(agent_status=agent_status).server_reachable is reachable
