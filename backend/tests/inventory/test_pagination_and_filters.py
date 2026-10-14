"""Cursor encode/decode, tamper rejection, and the query-parameter allowlist.

Both halves of docs/API_CONTRACTS.md §5's "opaque, signed" requirement are
asserted here: a round trip works, and every way of interfering with a cursor
produces ``422 invalid_cursor`` rather than a silently different page. §6's
unknown-parameter rule is in the same file because it is the same class of
bug — a request the server quietly reinterprets instead of refusing.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from fastapi import Request

from app.core.filters import UnknownQueryParameterError, reject_unknown_query_parameters
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor


def _request(query: str) -> Request:
    """A Starlette request carrying only a query string."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/servers",
            "headers": [],
            "query_string": query.encode("ascii"),
        }
    )


class TestCursor:
    def test_a_cursor_round_trips(self) -> None:
        token = encode_cursor(key="web-01", row_id="0199", sort="display_name", order="asc")
        cursor = decode_cursor(token, sort="display_name", order="asc")
        assert (cursor.key, cursor.id, cursor.sort, cursor.order) == (
            "web-01",
            "0199",
            "display_name",
            "asc",
        )

    def test_a_timestamp_key_round_trips(self) -> None:
        moment = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
        token = encode_cursor(key=moment, row_id="0199", sort="created_at", order="desc")
        assert decode_cursor(token, sort="created_at", order="desc").key == moment.isoformat()

    def test_a_cursor_is_opaque(self) -> None:
        """Not a secrecy claim — §5 requires opacity so clients cannot build one."""
        token = encode_cursor(key="web-01", row_id="0199", sort="display_name", order="asc")
        assert "web-01" not in token
        assert "." in token

    def test_a_tampered_payload_is_rejected(self) -> None:
        """The whole point of the signature: an edited key must not page."""
        token = encode_cursor(key="web-01", row_id="0199", sort="display_name", order="asc")
        body, signature = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload["k"] = "zzz-everything-after-this"
        body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        forged = base64.urlsafe_b64encode(body_bytes).decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(f"{forged}.{signature}", sort="display_name", order="asc")

    def test_a_tampered_signature_is_rejected(self) -> None:
        token = encode_cursor(key="web-01", row_id="0199", sort="display_name", order="asc")
        body, _ = token.split(".")
        with pytest.raises(InvalidCursorError):
            decode_cursor(f"{body}.AAAAAAAAAAAAAAAAAAAAAA", sort="display_name", order="asc")

    @pytest.mark.parametrize("token", ["", "nodot", "a.b", "....", "!!!.???", "eyJrIjoieCJ9"])
    def test_a_malformed_cursor_is_rejected(self, token: str) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, sort="display_name", order="asc")

    def test_a_cursor_minted_for_another_sort_is_rejected(self) -> None:
        """A valid signature is not enough: the key belongs to one column."""
        token = encode_cursor(key="web-01", row_id="0199", sort="display_name", order="asc")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, sort="created_at", order="asc")

    def test_a_cursor_minted_for_the_other_direction_is_rejected(self) -> None:
        token = encode_cursor(key="web-01", row_id="0199", sort="display_name", order="asc")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token, sort="display_name", order="desc")

    def test_the_error_is_a_422_naming_the_cursor_field(self) -> None:
        error = InvalidCursorError()
        assert error.status_code == 422
        assert error.code == "validation_failed"
        assert error.details == {
            "fields": [
                {
                    "loc": ["query", "cursor"],
                    "code": "invalid_cursor",
                    "message": "The cursor is not valid for this query.",
                }
            ]
        }


class TestQueryParameterAllowlist:
    def test_declared_parameters_pass(self) -> None:
        reject_unknown_query_parameters(
            _request("limit=10&sort=hostname"), frozenset({"limit", "sort"})
        )

    def test_an_undeclared_parameter_is_rejected(self) -> None:
        """§6. A typo'd filter that is ignored returns the *unfiltered* set."""
        with pytest.raises(UnknownQueryParameterError) as caught:
            reject_unknown_query_parameters(
                _request("enviroment=production"), frozenset({"environment", "limit"})
            )
        details = caught.value.details
        assert details is not None
        field = details["fields"][0]
        assert field["loc"] == ["query", "enviroment"]
        assert field["code"] == "unknown_field"
        assert field["allowed"] == ["environment", "limit"]

    def test_every_unknown_parameter_is_reported(self) -> None:
        with pytest.raises(UnknownQueryParameterError) as caught:
            reject_unknown_query_parameters(_request("one=1&two=2&limit=5"), frozenset({"limit"}))
        details = caught.value.details
        assert details is not None
        assert [field["loc"][1] for field in details["fields"]] == ["one", "two"]
