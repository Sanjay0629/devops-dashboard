"""The window of a history or audit read (docs/API_CONTRACTS.md §23, §24).

One rule set shared by three endpoints, so it is tested once, here: the defaults, the two ``422``s
(``from`` after ``to``, a range over the maximum -- never a clamp) and the audit rule that a range
beyond seven days needs **both** ends stated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import FieldValidationError
from app.core.window import resolve_window

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
AUDIT = {"explicit_beyond": timedelta(days=7)}


def _code(error: pytest.ExceptionInfo[FieldValidationError]) -> str:
    fields = error.value.details["fields"]  # type: ignore[index]
    return str(fields[0]["code"])


def test_nothing_given_is_the_last_seven_days_ending_now() -> None:
    window = resolve_window(None, None, now=NOW)
    assert window.end == NOW
    assert window.start == NOW - timedelta(days=7)


def test_only_to_given_looks_seven_days_back_from_it() -> None:
    end = NOW - timedelta(days=30)
    window = resolve_window(None, end, now=NOW)
    assert (window.start, window.end) == (end - timedelta(days=7), end)


def test_only_from_given_runs_to_now() -> None:
    start = NOW - timedelta(days=2)
    assert resolve_window(start, None, now=NOW).end == NOW


def test_ninety_days_is_allowed_and_ninety_and_a_second_is_refused_not_clamped() -> None:
    assert resolve_window(NOW - timedelta(days=90), NOW, now=NOW).start == NOW - timedelta(days=90)
    with pytest.raises(FieldValidationError) as error:
        resolve_window(NOW - timedelta(days=90, seconds=1), NOW, now=NOW)
    assert _code(error) == "range_too_large"


def test_from_after_to_is_refused() -> None:
    with pytest.raises(FieldValidationError) as error:
        resolve_window(NOW, NOW - timedelta(hours=1), now=NOW)
    assert _code(error) == "from_after_to"


def test_an_empty_window_is_allowed() -> None:
    assert resolve_window(NOW, NOW, now=NOW).start == NOW


@pytest.mark.parametrize("which", ["from", "to"])
def test_a_time_without_an_offset_is_refused(which: str) -> None:
    naive = datetime(2026, 10, 21, 12, 0)  # noqa: DTZ001 -- the point of the test
    start, end = (naive, None) if which == "from" else (None, naive)
    with pytest.raises(FieldValidationError) as error:
        resolve_window(start, end, now=NOW)
    assert _code(error) == "timezone_required"


class TestAuditSevenDayRule:
    def test_the_default_window_is_exactly_seven_days_and_needs_nothing(self) -> None:
        assert resolve_window(None, None, now=NOW, **AUDIT).start == NOW - timedelta(days=7)

    def test_both_ends_given_beyond_seven_days_is_allowed(self) -> None:
        window = resolve_window(NOW - timedelta(days=30), NOW, now=NOW, **AUDIT)
        assert window.start == NOW - timedelta(days=30)

    def test_from_alone_beyond_seven_days_is_refused_because_it_would_run_to_now(self) -> None:
        with pytest.raises(FieldValidationError) as error:
            resolve_window(NOW - timedelta(days=30), None, now=NOW, **AUDIT)
        assert _code(error) == "range_requires_from_and_to"

    def test_to_alone_is_never_beyond_seven_days_so_it_needs_nothing_more(self) -> None:
        assert resolve_window(None, NOW - timedelta(days=60), now=NOW, **AUDIT)

    def test_the_ninety_day_maximum_still_applies_with_both_ends_given(self) -> None:
        with pytest.raises(FieldValidationError) as error:
            resolve_window(NOW - timedelta(days=91), NOW, now=NOW, **AUDIT)
        assert _code(error) == "range_too_large"
