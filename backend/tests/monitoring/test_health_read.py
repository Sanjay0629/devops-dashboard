"""Health as the read path presents it (M2.5 commit 3), without a database.

``classify_health`` is the time arithmetic; this is everything around it that
decides whether to *ask* it: the gates that stop a retired, unsupported,
unmonitored or instance-less server being judged by silence nobody was
listening to, and the reference (D2) without which nothing is judged at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.inventory.models import Server
from app.monitoring.health import HealthThresholdTable
from app.monitoring.read import Reference, classify_reference, evaluate

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
TABLE = HealthThresholdTable.model_validate({"production": {"stale_after_seconds": 120}})
FRESH = Reference("ok", NOW, None)


def server(
    *,
    age: float | None = 5,
    status: str = "MANAGED",
    operating_system: str = "LINUX",
    monitoring_enabled: bool = True,
    metrics_instance: str | None = "web1.internal:9100",
    environment: str = "staging",
) -> Server:
    return Server(
        status=status,
        operating_system=operating_system,
        monitoring_enabled=monitoring_enabled,
        metrics_instance=metrics_instance,
        environment=environment,
        last_seen_at=None if age is None else NOW - timedelta(seconds=age),
    )


# --- the reference --------------------------------------------------------------------


def test_a_recent_answer_is_a_usable_reference() -> None:
    reference = classify_reference(
        configured=True, at=NOW - timedelta(seconds=30), now=NOW, fresh_within_seconds=90
    )
    assert reference.data_state == "ok"
    assert reference.usable == NOW - timedelta(seconds=30)


def test_the_freshness_bound_is_strict() -> None:
    on = classify_reference(
        configured=True, at=NOW - timedelta(seconds=90), now=NOW, fresh_within_seconds=90
    )
    over = classify_reference(
        configured=True, at=NOW - timedelta(seconds=91), now=NOW, fresh_within_seconds=90
    )
    assert on.data_state == "ok"
    assert over.data_state == "stale"


def test_an_old_answer_is_stale_and_unusable() -> None:
    """During an outage the marker stops; judging hosts against it would freeze them
    in a verdict nobody is confirming. It is dated, not used."""
    at = NOW - timedelta(hours=2)
    reference = classify_reference(configured=True, at=at, now=NOW, fresh_within_seconds=90)
    assert (reference.data_state, reference.usable, reference.at) == ("stale", None, at)


def test_no_answer_is_unavailable() -> None:
    reference = classify_reference(configured=True, at=None, now=NOW, fresh_within_seconds=90)
    assert (reference.data_state, reference.usable, reference.at) == ("unavailable", None, None)


def test_no_prometheus_is_not_configured_whatever_the_marker_says() -> None:
    reference = classify_reference(configured=False, at=NOW, now=NOW, fresh_within_seconds=90)
    assert (reference.data_state, reference.usable) == ("not_configured", None)


def test_every_unusable_reference_has_a_reason() -> None:
    for at in (None, NOW - timedelta(hours=2)):
        assert classify_reference(configured=True, at=at, now=NOW, fresh_within_seconds=90).reason
    assert classify_reference(configured=False, at=None, now=NOW, fresh_within_seconds=90).reason


# --- evaluating one server -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "state", "data_state"),
    [
        (5, "healthy", "ok"),
        (31, "warning", "ok"),
        (301, "stale", "stale"),
        (901, "unreachable", "stale"),
    ],
)
def test_an_evaluable_server_gets_the_time_verdict(age: float, state: str, data_state: str) -> None:
    result = evaluate(server(age=age), TABLE, FRESH)
    assert (result.state, result.data_state, result.unavailable_reason) == (
        state,
        data_state,
        None,
    )
    assert result.collected_at == NOW - timedelta(seconds=age)
    assert result.stale_after_seconds == 300


def test_the_environment_picks_the_lines() -> None:
    assert evaluate(server(age=200, environment="production"), TABLE, FRESH).state == "stale"
    assert evaluate(server(age=200, environment="staging"), TABLE, FRESH).state == "warning"
    assert evaluate(server(environment="production"), TABLE, FRESH).stale_after_seconds == 120


@pytest.mark.parametrize(
    ("changes", "data_state"),
    [
        ({"status": "RETIRED"}, "unavailable"),
        ({"operating_system": "WINDOWS"}, "unsupported"),
        ({"operating_system": "OTHER"}, "unsupported"),
        ({"monitoring_enabled": False}, "not_configured"),
        ({"metrics_instance": None}, "not_configured"),
        ({"age": None}, "unavailable"),
    ],
)
def test_a_server_nobody_is_watching_is_unknown_with_a_reason(
    changes: dict[str, object], data_state: str
) -> None:
    """Even when its ``last_seen_at`` is ancient: silence nobody listened to is not news."""
    base: dict[str, object] = {"age": 100_000}
    result = evaluate(server(**{**base, **changes}), TABLE, FRESH)  # type: ignore[arg-type]
    assert (result.state, result.data_state) == ("unknown", data_state)
    assert result.unavailable_reason
    assert result.collected_at is None


def test_a_pending_server_is_judged_like_any_other_scraped_server() -> None:
    """Registration grants no permission, but monitoring is read-only (PRD §4.3)."""
    assert evaluate(server(status="PENDING", age=5), TABLE, FRESH).state == "healthy"


@pytest.mark.parametrize("data_state", ["stale", "unavailable", "not_configured"])
def test_without_a_usable_reference_nothing_is_judged(data_state: str) -> None:
    """``null``, not ``unknown``: no evaluation happened, so no verdict exists."""
    reference = Reference(data_state, NOW - timedelta(hours=2), "why")  # type: ignore[arg-type]
    for case in (server(age=5), server(age=100_000), server(status="RETIRED")):
        result = evaluate(case, TABLE, reference)
        assert result.state is None
        assert result.data_state == data_state
        assert result.unavailable_reason == "why"


def test_the_stale_flag_follows_the_verdict() -> None:
    assert evaluate(server(age=5), TABLE, FRESH).stale is False
    assert evaluate(server(age=100), TABLE, FRESH).stale is False  # warning is not stale
    assert evaluate(server(age=301), TABLE, FRESH).stale is True
    assert evaluate(server(age=901), TABLE, FRESH).stale is True
    # Unknown is not "not stale": reporting False would claim a freshness nobody saw.
    assert evaluate(server(age=None), TABLE, FRESH).stale is None
    assert evaluate(server(age=5), TABLE, Reference("unavailable", None, "why")).stale is None
