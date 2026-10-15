"""Query-bound guard arithmetic (docs/API_CONTRACTS.md §15.2, §15.3).

Boundaries, not middles. Every bound is tested at `max`, at `max + 1` and —
where the arithmetic has an off-by-one in it — at the value that makes the
division come out exactly, because that is the only place a floor and a
ceiling disagree and the only place a wrong one lets an over-large query
through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import FieldValidationError
from app.metrics.service import (
    MAX_ENVIRONMENTS,
    MAX_POINTS,
    MAX_RANGE_SECONDS,
    MAX_SERVERS,
    MAX_TEMPLATES,
    MIN_STEP_SECONDS,
    QueryLimitExceededError,
    check_selection,
    check_time_range,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def span(seconds: float, step: int = MIN_STEP_SECONDS) -> int:
    return check_time_range(start=NOW, end=NOW + timedelta(seconds=seconds), step_seconds=step)


# --- the bounds the contract publishes --------------------------------------


def test_the_constants_are_the_numbers_the_contract_names() -> None:
    """Transcribed from §15.2 and §15.3, so a drift shows up here.

    Pinned rather than derived: these came from the contract, and a test that
    recomputed them from the code would agree with any value the code chose.
    """
    assert MAX_RANGE_SECONDS == 30 * 24 * 3600
    assert MAX_POINTS == 11_000
    assert MIN_STEP_SECONDS == 15
    assert MAX_TEMPLATES == 5
    assert MAX_SERVERS == 20
    assert MAX_ENVIRONMENTS == 4


# --- time range --------------------------------------------------------------


def test_a_range_within_every_bound_returns_its_point_count() -> None:
    """One hour at a 60-second step is 61 points: Prometheus includes `start`."""
    assert span(3600, step=60) == 61


def test_from_must_precede_to() -> None:
    """A field relationship, so a per-field 422 rather than a limit error.

    §8's field list is the useful answer here — the client has to know *which*
    parameter to change, and "the range limit" would be a lie about why.
    """
    with pytest.raises(FieldValidationError) as caught:
        check_time_range(start=NOW, end=NOW, step_seconds=60)
    assert caught.value.details == {
        "fields": [
            {
                "loc": ["query", "from"],
                "code": "min_value",
                "message": "'from' must be earlier than 'to'.",
            }
        ]
    }
    with pytest.raises(FieldValidationError):
        check_time_range(start=NOW, end=NOW - timedelta(seconds=1), step_seconds=60)


def test_the_step_floor_is_a_cost_limit() -> None:
    """A smaller step is strictly more points, so it is a limit, not a bound."""
    assert span(3600, step=MIN_STEP_SECONDS) == 241
    with pytest.raises(QueryLimitExceededError) as caught:
        span(3600, step=MIN_STEP_SECONDS - 1)
    assert caught.value.details == {
        "limit": "step_seconds",
        "maximum": MIN_STEP_SECONDS,
        "requested": MIN_STEP_SECONDS - 1,
    }


def test_thirty_days_is_allowed_and_thirty_days_plus_a_second_is_not() -> None:
    # A step that keeps the point count inside its own cap, so this test
    # measures the range bound and not the one after it.
    step = 3600
    assert span(MAX_RANGE_SECONDS, step=step) == 721
    with pytest.raises(QueryLimitExceededError) as caught:
        span(MAX_RANGE_SECONDS + 1, step=step)
    assert caught.value.details is not None
    assert caught.value.details["limit"] == "range_seconds"
    assert caught.value.details["maximum"] == MAX_RANGE_SECONDS


def test_the_point_cap_counts_the_endpoint() -> None:
    """The off-by-one that matters.

    A span of exactly ``(MAX_POINTS - 1) * step`` yields ``MAX_POINTS``
    points, because Prometheus returns one at ``start`` as well. A guard that
    floored the division would call that ``MAX_POINTS - 1`` and then admit a
    query one point over the cap.
    """
    step = MIN_STEP_SECONDS
    assert span((MAX_POINTS - 1) * step, step=step) == MAX_POINTS
    with pytest.raises(QueryLimitExceededError) as caught:
        span(MAX_POINTS * step, step=step)
    assert caught.value.details == {
        "limit": "points",
        "maximum": MAX_POINTS,
        "requested": MAX_POINTS + 1,
    }


def test_a_sub_step_remainder_does_not_add_a_point() -> None:
    """Integer division, deliberately: Prometheus emits no partial point."""
    step = 60
    assert span(120, step=step) == 3
    assert span(179, step=step) == 3
    assert span(180, step=step) == 4


def test_the_error_is_a_422_with_the_published_code() -> None:
    """§15.2: "returns 422 query_limit_exceeded with the allowed maximum"."""
    error = QueryLimitExceededError("points", MAX_POINTS, MAX_POINTS + 1)
    assert error.status_code == 422
    assert error.code == "query_limit_exceeded"
    assert error.details is not None
    assert error.details["maximum"] == MAX_POINTS
    # The message never carries the query, the server list or a hostname
    # (docs/SECURITY.md §20).
    assert "points" in error.message
    assert "instance" not in error.message


# --- fan-out ----------------------------------------------------------------


def test_between_one_and_five_templates() -> None:
    check_selection(template_count=1)
    check_selection(template_count=MAX_TEMPLATES)
    with pytest.raises(FieldValidationError):
        check_selection(template_count=0)
    with pytest.raises(QueryLimitExceededError) as caught:
        check_selection(template_count=MAX_TEMPLATES + 1)
    assert caught.value.details is not None
    assert caught.value.details["limit"] == "templates"


def test_at_most_twenty_servers() -> None:
    check_selection(template_count=1, server_count=MAX_SERVERS)
    with pytest.raises(QueryLimitExceededError) as caught:
        check_selection(template_count=1, server_count=MAX_SERVERS + 1)
    assert caught.value.details is not None
    assert caught.value.details["limit"] == "servers"


def test_at_most_four_environments() -> None:
    check_selection(template_count=1, environment_count=MAX_ENVIRONMENTS)
    with pytest.raises(QueryLimitExceededError) as caught:
        check_selection(template_count=1, environment_count=MAX_ENVIRONMENTS + 1)
    assert caught.value.details is not None
    assert caught.value.details["limit"] == "environments"


def test_no_servers_in_scope_is_an_empty_answer_not_a_limit_failure() -> None:
    """§15.3 drops out-of-scope servers and reports ``excluded_count``.

    So a comparison that scopes down to nothing is a successful empty answer.
    Raising here would turn "you may not see those servers" into "your request
    was malformed", and would also leak that the servers exist.
    """
    check_selection(template_count=1, server_count=0)


def test_the_worst_legal_request_is_still_bounded() -> None:
    """The product of every cap, which is what actually reaches Prometheus.

    5 templates x 20 servers x 11 000 points is 1.1 million values — large,
    and bounded, which is the claim. It is recorded here because the guards
    are individually reasonable and only their product is worth knowing.
    """
    check_selection(template_count=MAX_TEMPLATES, server_count=MAX_SERVERS)
    assert span((MAX_POINTS - 1) * MIN_STEP_SECONDS) == MAX_POINTS
    assert MAX_TEMPLATES * MAX_SERVERS * MAX_POINTS == 1_100_000
