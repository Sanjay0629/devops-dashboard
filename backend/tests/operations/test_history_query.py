"""The history query's pure parts: the allowlists, the cursor's pin, and the keyset's NULL rule.

What the statement *returns* needs real rows and lives in
``tests/integration/test_history_and_audit_scope.py``. Here: a closed set stays closed, a cursor
minted for one question cannot answer another, and a cursor that is validly signed but unusable is
``422 invalid_cursor`` and not a crash.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from app.core.errors import FieldValidationError
from app.core.pagination import (
    NULL_KEY,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    keyset_after,
    keyset_order,
)
from app.core.window import Window
from app.operations.history import HistoryFilters, history_query, validate_paging
from app.operations.models import Operation

WINDOW = Window(start=datetime(2026, 10, 1, tzinfo=UTC), end=datetime(2026, 10, 22, tzinfo=UTC))
CALLER = uuid.uuid4()


def _sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _query(filters: HistoryFilters | None = None, **overrides: object) -> object:
    arguments: dict[str, object] = {
        "sort": "created_at",
        "order": "desc",
        "cursor_token": None,
        "limit": 50,
        **overrides,
    }
    return history_query([], CALLER, filters or HistoryFilters(), WINDOW, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "filters",
    [
        HistoryFilters(execution_status=("DONE",)),
        HistoryFilters(execution_status=("SUCCEEDED", "VERIFYING")),  # never stored (OPEN-69)
        HistoryFilters(action_type=("RESTART",)),
        HistoryFilters(action_type=("exec",)),
        HistoryFilters(environment=("prod",)),
        HistoryFilters(environment=("*",)),
    ],
)
def test_a_value_outside_a_closed_set_is_refused(filters: HistoryFilters) -> None:
    with pytest.raises(FieldValidationError):
        _query(filters)


@pytest.mark.parametrize(
    ("sort", "order", "limit"),
    [
        ("status", "desc", 50),  # not on the allowlist
        ("created_at; DROP TABLE operations", "desc", 50),
        ("created_at", "sideways", 50),
        ("created_at", "desc", 0),
        ("created_at", "desc", 201),  # §5: more than the maximum is a 422, never a clamp
    ],
)
def test_sort_order_and_limit_are_allowlisted_and_bounded(
    sort: str, order: str, limit: int
) -> None:
    with pytest.raises(FieldValidationError):
        validate_paging(sort, order, limit)


def test_the_statement_selects_one_page_plus_one_row() -> None:
    assert "LIMIT 51" in _sql(_query())
    assert "LIMIT 201" in _sql(_query(limit=200))


def test_scope_and_the_requester_are_in_the_where_clause_not_applied_afterwards() -> None:
    text = _sql(_query())
    # With no grants the scope half is `false`, so only the caller's own rows can match.
    assert f"operations.requested_by = '{CALLER}'" in text
    assert "operations.created_at >=" in text and "operations.created_at <=" in text


def test_the_environment_filter_reads_the_stored_column_not_the_servers_label() -> None:
    text = _sql(_query(HistoryFilters(environment=("production",))))
    assert "operations.environment IN ('production')" in text
    where = text.split("WHERE", 1)[1]  # the select list carries every server column
    assert "servers.environment" not in where


def test_requested_by_narrows_and_is_and_ed_with_scope() -> None:
    other = uuid.uuid4()
    text = _sql(_query(HistoryFilters(requested_by=other)))
    assert f"operations.requested_by = '{other}'" in text
    # It sits beside the visibility condition; it does not replace it.
    assert f"operations.requested_by = '{CALLER}'" in text


class TestCursorPin:
    def test_a_cursor_minted_for_other_filters_is_refused(self) -> None:
        mine = HistoryFilters(environment=("staging",))
        token = encode_cursor(
            key=datetime(2026, 10, 10, tzinfo=UTC),
            row_id=uuid.uuid4(),
            sort=mine.fingerprint("created_at"),
            order="desc",
        )
        _query(mine, cursor_token=token)  # the question it was minted for
        with pytest.raises(InvalidCursorError):
            _query(HistoryFilters(environment=("production",)), cursor_token=token)

    def test_a_cursor_minted_for_another_sort_or_direction_is_refused(self) -> None:
        filters = HistoryFilters()
        token = encode_cursor(
            key=datetime(2026, 10, 10, tzinfo=UTC),
            row_id=uuid.uuid4(),
            sort=filters.fingerprint("created_at"),
            order="desc",
        )
        with pytest.raises(InvalidCursorError):
            _query(filters, cursor_token=token, sort="completed_at")
        with pytest.raises(InvalidCursorError):
            _query(filters, cursor_token=token, order="asc")

    def test_the_fingerprint_ignores_the_order_values_are_given_in(self) -> None:
        a = HistoryFilters(environment=("staging", "production"))
        b = HistoryFilters(environment=("production", "staging"))
        assert a.fingerprint("created_at") == b.fingerprint("created_at")
        assert a.fingerprint("created_at") != a.fingerprint("completed_at")


class TestKeysetNulls:
    def _cursor(self, key: str) -> object:
        token = encode_cursor(key=key, row_id=uuid.uuid4(), sort="s", order="desc")
        return decode_cursor(token, sort="s", order="desc")

    def test_a_null_position_continues_only_through_null_rows(self) -> None:
        cursor = self._cursor(NULL_KEY)
        text = _sql(
            keyset_after(
                Operation.completed_at,
                Operation.id,
                cursor,
                order="desc",
                nullable=True,  # type: ignore[arg-type]
            )
        )
        assert "operations.completed_at IS NULL" in text
        assert "operations.id <" in text and "OR" not in text

    def test_a_dated_position_is_followed_by_later_dates_and_then_the_null_rows(self) -> None:
        cursor = self._cursor(datetime(2026, 10, 10, tzinfo=UTC).isoformat())
        text = _sql(
            keyset_after(
                Operation.completed_at,
                Operation.id,
                cursor,
                order="desc",
                nullable=True,  # type: ignore[arg-type]
            )
        )
        assert "(operations.completed_at, operations.id) <" in text
        assert "OR operations.completed_at IS NULL" in text

    def test_null_rows_sort_last_in_both_directions(self) -> None:
        for order in ("asc", "desc"):
            clauses = keyset_order(Operation.completed_at, Operation.id, order=order, nullable=True)
            assert "NULLS LAST" in _sql(clauses[0])

    def test_the_null_key_on_a_column_that_cannot_be_null_is_an_invalid_cursor(self) -> None:
        cursor = self._cursor(NULL_KEY)
        with pytest.raises(InvalidCursorError):
            keyset_after(Operation.created_at, Operation.id, cursor, order="desc")  # type: ignore[arg-type]

    def test_a_signed_key_that_is_not_a_timestamp_is_an_invalid_cursor_not_a_crash(self) -> None:
        cursor = self._cursor("web-01")
        with pytest.raises(InvalidCursorError):
            keyset_after(Operation.created_at, Operation.id, cursor, order="desc")  # type: ignore[arg-type]

    def test_a_signed_id_that_is_not_a_uuid_is_an_invalid_cursor_not_a_crash(self) -> None:
        token = encode_cursor(key=datetime.now(UTC), row_id="not-a-uuid", sort="s", order="desc")
        cursor = decode_cursor(token, sort="s", order="desc")
        with pytest.raises(InvalidCursorError):
            keyset_after(Operation.created_at, Operation.id, cursor, order="desc")
