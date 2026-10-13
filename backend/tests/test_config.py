"""Configuration loading: fail fast, no secret defaults, no unsafe combinations."""

from __future__ import annotations

import pytest

from app.core.config import ConfigurationError, Settings, load_settings
from app.core.redaction import MARKER


def test_loads_with_full_environment(settings: Settings) -> None:
    assert settings.environment == "local"
    assert settings.api_base_path == "/api/v1"


@pytest.mark.parametrize("missing", ["DCP_DATABASE_URL", "DCP_REDIS_URL"])
def test_missing_credential_fails_fast(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """A credential has no default: the process refuses to start without it."""
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert missing in str(caught.value)


def test_error_message_does_not_leak_the_offending_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DCP_DATABASE_STATEMENT_TIMEOUT_MS", "not-a-number")
    monkeypatch.setenv("DCP_DATABASE_URL", "postgresql+psycopg://u:canary-secret-value@h:5432/d")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "canary-secret-value" not in str(caught.value)


def test_credentials_are_not_printable(settings: Settings) -> None:
    """SecretStr keeps the value out of repr, str and f-strings."""
    assert "placeholder@localhost" not in repr(settings)
    assert "placeholder@localhost" not in str(settings.database_url)
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")


def test_unknown_setting_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_TOTALLY_UNKNOWN_SETTING", "1")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_CORS_ORIGINS", "https://example.invalid,*")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "cors_origins" in str(caught.value).lower()


def test_cors_origins_split_from_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DCP_CORS_ORIGINS", "https://a.invalid, https://b.invalid ,,")
    assert load_settings().cors_origins == ("https://a.invalid", "https://b.invalid")


def test_demo_mode_rejected_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """AD-15: a real deployment can never accidentally serve synthetic data."""
    monkeypatch.setenv("DCP_ENVIRONMENT", "production")
    monkeypatch.setenv("DCP_DEMO_MODE", "true")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "demo_mode" in str(caught.value)


def test_debug_logging_rejected_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_ENVIRONMENT", "staging")
    monkeypatch.setenv("DCP_LOG_LEVEL", "DEBUG")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_demo_mode_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DCP_DEMO_MODE", raising=False)
    assert load_settings().demo_mode is False


@pytest.mark.parametrize("bad_path", ["api/v1", "/api/v1/", ""])
def test_api_base_path_validation(monkeypatch: pytest.MonkeyPatch, bad_path: str) -> None:
    monkeypatch.setenv("DCP_API_BASE_PATH", bad_path)
    with pytest.raises(ConfigurationError):
        load_settings()


def test_marker_is_fixed_width_and_leaks_nothing() -> None:
    """Guards the redaction contract: the marker never varies with the input."""
    assert MARKER == "[REDACTED]"
