"""The audit query's pure parts: closed filters, what ``AUDIT_QUERIED`` may record, the cursor pin.

What the statement returns, and which events a grant reaches, needs real rows and lives in
``tests/integration/test_history_and_audit_scope.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from app.audit.query import AuditFilters, audit_event_query, audit_query
from app.authz.matcher import Grant
from app.core.errors import FieldValidationError
from app.core.pagination import InvalidCursorError, encode_cursor
from app.core.window import Window

WINDOW = Window(start=datetime(2026, 10, 1, tzinfo=UTC), end=datetime(2026, 10, 22, tzinfo=UTC))
ADMIN = [Grant("audit", "read", "ALLOW", {"environment": ["*"]})]


def _sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _query(filters: AuditFilters | None = None, **overrides: object) -> object:
    arguments: dict[str, object] = {"order": "desc", "cursor_token": None, "limit": 50, **overrides}
    return audit_query(ADMIN, filters or AuditFilters(), WINDOW, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "filters",
    [
        AuditFilters(actor_type="ROBOT"),
        AuditFilters(outcome="OK"),
        AuditFilters(target_type="everything"),
        AuditFilters(environment="prod"),
        AuditFilters(environment="*"),
        AuditFilters(action_type="login_failed"),  # upper case only
        AuditFilters(action_type="LOGIN%"),  # an exact token, never a pattern
        AuditFilters(action_type="A" * 65),
        AuditFilters(action_type="X; DROP TABLE audit_events"),
    ],
)
def test_a_filter_outside_its_closed_set_is_refused(filters: AuditFilters) -> None:
    with pytest.raises(FieldValidationError):
        _query(filters)


@pytest.mark.parametrize(("order", "limit"), [("sideways", 50), ("desc", 0), ("desc", 201)])
def test_order_and_limit_are_bounded(order: str, limit: int) -> None:
    with pytest.raises(FieldValidationError):
        _query(order=order, limit=limit)


def test_an_unknown_action_type_is_a_valid_filter_because_the_catalogue_is_extensible() -> None:
    assert "action_type = 'SOMETHING_NEW'" in _sql(
        _query(AuditFilters(action_type="SOMETHING_NEW"))
    )


def test_scope_and_the_window_are_in_the_where_clause() -> None:
    text = _sql(_query())
    assert "audit_events.occurred_at >=" in text and "audit_events.occurred_at <=" in text
    assert "audit_events.environment IS NOT NULL" in text
    assert "audit_events.environment != 'production'" in text
    assert "LIMIT 51" in text


def test_a_caller_with_no_audit_grant_reaches_nothing() -> None:
    for build in (
        lambda: audit_query([], AuditFilters(), WINDOW, order="desc", cursor_token=None, limit=5),
        lambda: audit_event_query([], uuid.uuid4()),
    ):
        assert "false" in _sql(build()).lower().split("where", 1)[1]


def test_a_grant_for_another_permission_reaches_nothing() -> None:
    other = [Grant("operation", "read", "ALLOW", {"environment": ["*"]})]
    statement = audit_query(other, AuditFilters(), WINDOW, order="desc", cursor_token=None, limit=5)
    assert "false" in _sql(statement).lower().split("where", 1)[1]


class TestSupplied:
    """What ``AUDIT_QUERIED`` records: ids and enum values that were given, nothing else."""

    def test_only_the_filters_given_appear(self) -> None:
        actor = uuid.uuid4()
        filters = AuditFilters(actor_id=actor, outcome="DENIED")
        assert filters.supplied() == {"actor_id": str(actor), "outcome": "DENIED"}

    def test_no_filters_is_an_empty_record(self) -> None:
        assert AuditFilters().supplied() == {}


class TestCursorPin:
    def test_a_cursor_minted_for_other_filters_is_refused(self) -> None:
        mine = AuditFilters(outcome="DENIED")
        token = encode_cursor(
            key=datetime(2026, 10, 10, tzinfo=UTC),
            row_id=uuid.uuid4(),
            sort=mine.fingerprint(),
            order="desc",
        )
        _query(mine, cursor_token=token)
        with pytest.raises(InvalidCursorError):
            _query(AuditFilters(outcome="SUCCESS"), cursor_token=token)
        with pytest.raises(InvalidCursorError):
            _query(mine, cursor_token=token, order="asc")
