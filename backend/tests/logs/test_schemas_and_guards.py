"""The §19.1 request model and the bound guards."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.errors import FieldValidationError
from app.logs import guards
from app.logs.schemas import LogQueryRequest
from app.metrics.service import QueryLimitExceededError

SERVER = str(uuid.uuid4())
BASE: dict[str, Any] = {
    "from": "2026-01-15T08:00:00Z",
    "to": "2026-01-15T09:00:00Z",
    "server_ids": [SERVER],
}


def make(**overrides: Any) -> LogQueryRequest:
    body = {**BASE, **overrides}
    return LogQueryRequest.model_validate({k: v for k, v in body.items() if v is not ...})


def rejected(**overrides: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        make(**overrides)
    return caught.value


# --- the request model --------------------------------------------------------


def test_a_minimal_request_takes_the_documented_defaults() -> None:
    request = make()
    assert (request.limit, request.direction, request.cursor) == (200, "backward", None)
    assert request.from_ < request.to


@pytest.mark.parametrize(
    "field", ["query", "logql", "selector", "regex", "labels", "stream", "expr", "pipeline"]
)
def test_any_field_that_could_carry_logql_is_refused(field: str) -> None:
    assert any(e["type"] == "extra_forbidden" for e in rejected(**{field: '{a="b"}'}).errors())


@pytest.mark.parametrize("missing", ["from", "to"])
def test_the_time_range_is_required(missing: str) -> None:
    assert rejected(**{missing: ...})


def test_a_naive_timestamp_is_refused() -> None:
    assert rejected(**{"from": "2026-01-15T08:00:00"})
    assert rejected(to="2026-01-15T09:00:00")


@pytest.mark.parametrize(
    "extra", [{"application_id": str(uuid.uuid4())}, {"environment": "staging"}]
)
def test_any_one_target_is_enough(extra: dict[str, Any]) -> None:
    assert make(server_ids=..., **extra)


def test_no_target_at_all_is_refused() -> None:
    assert rejected(server_ids=...)


def test_an_empty_server_list_is_not_a_missing_one() -> None:
    assert rejected(server_ids=[], environment="staging")


def test_at_most_fifty_servers() -> None:
    assert make(server_ids=[str(uuid.uuid4()) for _ in range(50)])
    assert rejected(server_ids=[str(uuid.uuid4()) for _ in range(51)])


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_limit_bounds(limit: int) -> None:
    assert rejected(limit=limit)


@pytest.mark.parametrize("limit", [1, 1000])
def test_limit_bounds_are_inclusive(limit: int) -> None:
    assert make(limit=limit).limit == limit


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_type", "file"),
        ("source_type", "NGINX"),
        ("level", ["warning"]),
        ("level", ["info", "trace"]),
        ("level", []),
        ("environment", "prod"),
        ("pattern_preset", "http_4xx"),
        ("pattern_preset", ".*"),
        ("direction", "sideways"),
        ("service", 'x"} or {a="b'),
        ("service", "has space"),
        ("service", "-leading"),
        ("service", "x" * 64),
        ("service", ""),
    ],
)
def test_closed_values_and_shapes_are_enforced(field: str, value: Any) -> None:
    assert rejected(**{field: value})


@pytest.mark.parametrize("value", ["api.service", "other", "a", "x" * 63])
def test_service_names_inside_the_shape_are_accepted(value: str) -> None:
    assert make(service=value).service == value


def test_contains_is_bounded_and_printable() -> None:
    assert make(contains="a" * 256)
    assert rejected(contains="a" * 257)
    assert rejected(contains="")
    for bad in ("a\nb", "a\x00b", "a\x1bb", "a\x7fb"):
        assert rejected(contains=bad)


def test_contains_may_hold_logql_looking_text() -> None:
    """It is a literal; whether it *looks* like a query is irrelevant."""
    assert make(contains='"} or {a="b"}').contains == '"} or {a="b"}'


# --- the guards ---------------------------------------------------------------

NOW = datetime(2026, 1, 20, 12, 0, tzinfo=UTC)


def check(start: datetime, end: datetime, retention_days: int = 14) -> None:
    guards.check_time_range(start=start, end=end, now=NOW, retention_days=retention_days)


def test_a_sane_range_passes() -> None:
    check(NOW - timedelta(hours=1), NOW)


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-1)])
def test_an_empty_or_inverted_range_is_a_field_error(delta: timedelta) -> None:
    start = NOW - timedelta(hours=1)
    with pytest.raises(FieldValidationError):
        check(start, start + delta)


def test_the_range_cap_is_exactly_seven_days() -> None:
    start = NOW - timedelta(days=7)
    check(start, NOW)
    with pytest.raises(QueryLimitExceededError) as caught:
        check(start - timedelta(seconds=1), NOW)
    assert caught.value.details == {
        "limit": "range_seconds",
        "maximum": 7 * 86400,
        "requested": 7 * 86400 + 1,
    }


def test_retention_is_a_refusal_not_a_clamp() -> None:
    oldest = NOW - timedelta(days=14)
    check(oldest, oldest + timedelta(hours=1))
    with pytest.raises(QueryLimitExceededError) as caught:
        check(oldest - timedelta(seconds=1), oldest + timedelta(hours=1))
    assert caught.value.details is not None
    assert caught.value.details["limit"] == "retention_seconds"
    assert caught.value.details["maximum"] == 14 * 86400


def test_retention_follows_the_setting() -> None:
    start = NOW - timedelta(days=3)
    check(start, start + timedelta(hours=1), retention_days=3)
    with pytest.raises(QueryLimitExceededError):
        check(start - timedelta(seconds=1), start + timedelta(hours=1), retention_days=3)


def test_order_is_checked_before_span_and_retention() -> None:
    with pytest.raises(FieldValidationError):
        check(NOW - timedelta(days=30), NOW - timedelta(days=31))


def test_the_resolved_server_cap_is_inclusive() -> None:
    guards.check_resolved_servers(200)
    with pytest.raises(QueryLimitExceededError) as caught:
        guards.check_resolved_servers(201)
    assert caught.value.details == {"limit": "servers", "maximum": 200, "requested": 201}
