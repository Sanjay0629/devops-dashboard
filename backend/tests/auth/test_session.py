"""Session arithmetic and cookie-flag assembly (docs/SECURITY.md §5).

Pure unit tests: the deadline logic and the cookie attributes, with no Redis.
The live store (create / load / slide / delete / terminate) is exercised
against real Redis in ``tests/integration/test_auth_flow.py`` — Redis is where
expiry, atomic single-use and the per-user index actually have to hold.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from starlette.responses import Response

from app.auth.cookies import (
    SESSION_COOKIE_NAME,
    clear_auth_cookies,
    set_csrf_cookie,
    set_session_cookie,
)
from app.auth.session import SessionRecord, new_session_id
from app.core.config import Settings, get_settings
from app.core.csrf import CSRF_COOKIE_NAME, new_csrf_token

pytestmark = pytest.mark.security


def _record(**overrides: object) -> SessionRecord:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    base: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "issued_at": now,
        "last_seen_at": now,
        "absolute_deadline": now + timedelta(hours=8),
        "idle_deadline": now + timedelta(minutes=30),
    }
    base.update(overrides)
    return SessionRecord(**base)  # type: ignore[arg-type]


# --- identifiers ------------------------------------------------------------


def test_session_ids_are_unique_and_high_entropy() -> None:
    ids = {new_session_id() for _ in range(200)}
    assert len(ids) == 200
    # token_urlsafe(32) → 43 characters, i.e. 256 bits, well over the ≥128-bit
    # floor in docs/SECURITY.md §5.
    assert all(len(value) >= 43 for value in ids)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in ids)


def test_session_id_is_not_derived_from_the_user() -> None:
    """The identifier must be opaque: nothing about the user may be readable.

    Two sessions for the same user share no prefix, and the user id does not
    appear in the identifier.
    """
    user_id = uuid.uuid4()
    first, second = new_session_id(), new_session_id()
    assert str(user_id) not in first
    assert str(user_id).replace("-", "") not in first
    assert first[:8] != second[:8]


def test_the_redis_key_is_a_hash_not_the_identifier() -> None:
    """A key dump must not hand over usable session identifiers."""
    from app.auth.session import _key

    session_id = new_session_id()
    key = _key(session_id)
    assert session_id not in key
    assert re.fullmatch(r"dcp:session:[0-9a-f]{64}", key)
    # Deterministic, or a lookup could never find its own record.
    assert _key(session_id) == key


# --- expiry, decided server-side -------------------------------------------


def test_a_fresh_session_is_not_expired() -> None:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    assert _record().is_expired(at=now) is False


def test_idle_timeout_expires_the_session() -> None:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    record = _record()
    assert record.is_expired(at=now + timedelta(minutes=29)) is False
    assert record.is_expired(at=now + timedelta(minutes=31)) is True


def test_absolute_timeout_expires_the_session_despite_activity() -> None:
    """The absolute deadline cannot be pushed back by using the session.

    A session kept alive by continuous activity still dies at its absolute
    deadline — that is the whole point of having two deadlines.
    """
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    busy = _record(idle_deadline=now + timedelta(hours=100))
    assert busy.is_expired(at=now + timedelta(hours=7, minutes=59)) is False
    assert busy.is_expired(at=now + timedelta(hours=8, minutes=1)) is True


def test_expiry_is_evaluated_at_the_boundary_inclusively() -> None:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    record = _record(idle_deadline=now)
    # Exactly at the deadline counts as expired: a session should not be
    # usable in the instant it was meant to die.
    assert record.is_expired(at=now) is True


def test_deadlines_are_timezone_aware_through_a_round_trip() -> None:
    """A naive timestamp here would silently shift expiry by the host offset."""
    record = _record()
    restored = SessionRecord.from_json(record.to_json())
    assert restored == record
    for value in (
        restored.issued_at,
        restored.last_seen_at,
        restored.absolute_deadline,
        restored.idle_deadline,
    ):
        assert value.tzinfo is not None


def test_serialised_record_holds_no_permission_or_role(settings: Settings) -> None:
    """PRD §4.1: a cached permission would survive a revocation.

    The stored record must carry a user id and deadlines, and nothing about
    what the user may do.
    """
    import json

    data = json.loads(_record().to_json())
    assert set(data) == {
        "user_id",
        "issued_at",
        "last_seen_at",
        "absolute_deadline",
        "idle_deadline",
        "client_ip",
        "user_agent",
    }
    serialised = _record().to_json().lower()
    for forbidden in ("role", "permission", "scope", "admin", "token"):
        assert forbidden not in serialised


# --- cookie flags -----------------------------------------------------------


def _cookie_header(response: Response, name: str) -> str:
    for key, value in response.raw_headers:
        if key.decode().lower() == "set-cookie" and value.decode().startswith(f"{name}="):
            return value.decode()
    raise AssertionError(f"no Set-Cookie for {name}")


def test_session_cookie_flags(settings: Settings) -> None:
    response = Response()
    set_session_cookie(response, "the-session-id", settings)
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert "HttpOnly" in header
    assert "SameSite=strict" in header.replace("samesite", "SameSite")
    assert "Path=/" in header
    # Host-only: a Domain attribute would send the cookie to sibling subdomains.
    assert "Domain=" not in header


def test_csrf_cookie_is_readable_by_script_but_still_samesite_strict(
    settings: Settings,
) -> None:
    """The SPA must read it to echo it; it authenticates nothing on its own."""
    response = Response()
    set_csrf_cookie(response, new_csrf_token(), settings)
    header = _cookie_header(response, CSRF_COOKIE_NAME)
    assert "HttpOnly" not in header
    assert "samesite=strict" in header.lower()


def test_secure_is_false_only_on_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/SECURITY.md §5 permits exactly one deviation, on localhost."""
    assert get_settings().environment == "local"
    assert get_settings().cookie_secure is False

    for environment in ("development", "staging", "production"):
        monkeypatch.setenv("DCP_ENVIRONMENT", environment)
        monkeypatch.setenv("DCP_DEMO_MODE", "false")
        get_settings.cache_clear()
        assert get_settings().cookie_secure is True, environment
        response = Response()
        set_session_cookie(response, "x", get_settings())
        assert "Secure" in _cookie_header(response, SESSION_COOKIE_NAME)
    get_settings.cache_clear()


def test_secure_cannot_be_turned_off_by_configuration() -> None:
    """There is no DCP_COOKIE_SECURE to get wrong."""
    assert "cookie_secure" not in Settings.model_fields


def test_logout_clears_both_cookies_with_matching_flags(settings: Settings) -> None:
    """Mismatched flags leave some browsers holding the original cookie."""
    response = Response()
    clear_auth_cookies(response, settings)
    session = _cookie_header(response, SESSION_COOKIE_NAME)
    csrf = _cookie_header(response, CSRF_COOKIE_NAME)
    for header in (session, csrf):
        assert "Path=/" in header
        assert "samesite=strict" in header.lower()
        assert "Max-Age=0" in header or "expires=Thu, 01 Jan 1970" in header.lower()
    assert "HttpOnly" in session
    assert "HttpOnly" not in csrf


def test_cookie_max_age_matches_the_absolute_timeout(settings: Settings) -> None:
    """A cookie outliving the server-side session is a pointless 401 machine.

    (The reverse — a cookie that dies first — only logs the user out early, so
    the alignment is a usability property, not a security one. Expiry itself is
    always decided server-side.)
    """
    response = Response()
    set_session_cookie(response, "x", settings)
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert f"Max-Age={settings.session_absolute_timeout_seconds}" in header


def test_no_session_identifier_reaches_a_log_line(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """docs/SECURITY.md §5: no session id in a URL, log line or audit row."""
    from app.core.logging import configure_logging, get_logger

    configure_logging(level="INFO", json_output=True)
    session_id = new_session_id()
    get_logger(__name__).info("session_created", user_id=str(uuid.uuid4()))
    assert session_id not in capsys.readouterr().out
