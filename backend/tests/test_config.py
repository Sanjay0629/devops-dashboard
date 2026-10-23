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
    assert "placeholder@127.0.0.1" not in repr(settings)
    assert "placeholder@127.0.0.1" not in str(settings.database_url)
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


# --- Prometheus (M2.1) -------------------------------------------------------


def test_prometheus_is_optional_and_unset_means_unconfigured(settings: Settings) -> None:
    """PRD §11: the application starts without it and does not pretend.

    ``prometheus_configured`` is what every metrics path branches on, so the
    unset default has to be a real "no" rather than an empty string that
    reads as configured.
    """
    assert settings.prometheus_url is None
    assert settings.prometheus_configured is False
    assert settings.prometheus_timeout_seconds == 10.0


def test_prometheus_url_is_normalised_and_reported_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trailing slash goes, so the client can join a path onto it."""
    monkeypatch.setenv("DCP_PROMETHEUS_URL", "http://prometheus:9090/")
    loaded = load_settings()
    assert loaded.prometheus_url == "http://prometheus:9090"
    assert loaded.prometheus_configured is True


@pytest.mark.parametrize(
    "bad",
    [
        "prometheus:9090",
        "ftp://prometheus:9090",
        "file:///etc/passwd",
        "http://user:password@prometheus:9090",
        "http://prometheus:9090#fragment",
        "http://prometheus:9090 ",
        "http://prometheus:9090\nX-Injected: 1",
        "http:// prometheus:9090",
        "http://",
        "http:///no-host",
    ],
)
def test_a_url_that_would_be_fetched_is_validated_at_load_time(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """The three checks ``app/core/ssrf.py`` applies, enforced here instead.

    ``DCP_PROMETHEUS_URL`` deliberately does not go through the
    registered-URL path (see ``app/metrics/client.py``), so the scheme,
    credential, fragment and whitespace rules have to hold here or they hold
    nowhere. Rejecting at load time keeps all of it out of the request path.
    """
    monkeypatch.setenv("DCP_PROMETHEUS_URL", bad)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "DCP_PROMETHEUS_URL" in str(caught.value)
    # The rejected value is never echoed: it can carry a credential.
    assert "password" not in str(caught.value)


def test_the_same_checks_now_cover_the_oidc_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """One validator, three fields: the OIDC URLs are fetched or redirected to.

    A credential in ``DCP_OIDC_ISSUER`` would reach every log line that
    records the issuer, and a fragment there means the value was pasted out of
    a browser. Both were accepted before M2.1.
    """
    monkeypatch.setenv("DCP_OIDC_ISSUER", "http://user:secret@idp.invalid")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "DCP_OIDC_ISSUER" in str(caught.value)
    assert "secret" not in str(caught.value)


def test_prometheus_must_use_https_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/SECURITY.md §19.3 permits plain http for allowlisted hosts only.

    This URL is not allowlist-gated, so the remaining honest rule is https
    everywhere but ``local``. An internal Prometheus without TLS has to be
    fronted by something that has it.
    """
    monkeypatch.setenv("DCP_ENVIRONMENT", "production")
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    monkeypatch.setenv("DCP_PROMETHEUS_URL", "http://prometheus:9090")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "prometheus_url must use https" in str(caught.value)

    monkeypatch.setenv("DCP_PROMETHEUS_URL", "https://prometheus.example.invalid")
    assert load_settings().prometheus_url == "https://prometheus.example.invalid"


def test_the_prometheus_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbounded deadline is not a deadline (CLAUDE.md §5)."""
    for bad in ("0", "-1", "61"):
        monkeypatch.setenv("DCP_PROMETHEUS_TIMEOUT_SECONDS", bad)
        with pytest.raises(ConfigurationError):
            load_settings()


# --- Loki (M3.2) -----------------------------------------------------------------


def test_loki_is_optional_and_unset_means_unconfigured(settings: Settings) -> None:
    """``loki_configured`` is what every log read branches on (503 not_configured)."""
    assert settings.loki_url is None
    assert settings.loki_configured is False
    assert (settings.loki_timeout_seconds, settings.log_retention_days) == (15.0, 14)


def test_loki_url_is_normalised_and_reported_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_LOKI_URL", "http://loki:3100/")
    loaded = load_settings()
    assert loaded.loki_url == "http://loki:3100"
    assert loaded.loki_configured is True


@pytest.mark.parametrize(
    "bad",
    [
        "loki:3100",
        "ftp://loki:3100",
        "http://user:password@loki:3100",
        "http://loki:3100#fragment",
        "http://loki:3100\nX-Injected: 1",
        "http://",
    ],
)
def test_the_loki_url_is_validated_at_load_time(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("DCP_LOKI_URL", bad)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "DCP_LOKI_URL" in str(caught.value)
    assert "password" not in str(caught.value)


def test_loki_must_use_https_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_ENVIRONMENT", "production")
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    monkeypatch.setenv("DCP_LOKI_URL", "http://loki:3100")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "loki_url must use https" in str(caught.value)

    monkeypatch.setenv("DCP_LOKI_URL", "https://loki.example.invalid")
    assert load_settings().loki_url == "https://loki.example.invalid"


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("DCP_LOKI_TIMEOUT_SECONDS", "0"),
        ("DCP_LOKI_TIMEOUT_SECONDS", "-1"),
        ("DCP_LOKI_TIMEOUT_SECONDS", "61"),
        ("DCP_LOG_RETENTION_DAYS", "0"),
        ("DCP_LOG_RETENTION_DAYS", "-1"),
        ("DCP_LOG_RETENTION_DAYS", "3651"),
    ],
)
def test_the_loki_deadline_and_retention_are_bounded(
    monkeypatch: pytest.MonkeyPatch, name: str, bad: str
) -> None:
    monkeypatch.setenv(name, bad)
    with pytest.raises(ConfigurationError):
        load_settings()


# --- Jenkins (M5.1, OPEN-119) --------------------------------------------------------------

JENKINS_OK = {
    "DCP_JENKINS_URL": "http://mock-jenkins:8080",
    "DCP_JENKINS_USER": "dcp",
    "DCP_JENKINS_TOKEN_REFERENCE": "env://JENKINS_TOKEN",
    "DCP_SSRF_HOST_ALLOWLIST": "mock-jenkins",
    "DCP_SSRF_INSECURE_HOSTS": "mock-jenkins",
}


def _jenkins(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for key, value in {**JENKINS_OK, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_jenkins_is_unset_by_default(settings: Settings) -> None:
    assert settings.jenkins_url is None and settings.jenkins_token_reference is None


def test_a_complete_jenkins_configuration_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    _jenkins(monkeypatch)
    assert load_settings().jenkins_url == "http://mock-jenkins:8080"


def test_blank_jenkins_variables_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes an unset variable as the empty string."""
    for name in ("URL", "USER", "TOKEN_REFERENCE"):
        monkeypatch.setenv(f"DCP_JENKINS_{name}", "")
    assert load_settings().jenkins_url is None


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "DCP_SSRF_HOST_ALLOWLIST": "somewhere-else",
            "DCP_SSRF_INSECURE_HOSTS": "",
        },  # not allowlisted
        {"DCP_SSRF_HOST_ALLOWLIST": ""},  # nothing allowlisted admits nothing
        {"DCP_SSRF_INSECURE_HOSTS": ""},  # http, but the host is not insecure-permitted
        {"DCP_JENKINS_USER": None},
        {"DCP_JENKINS_TOKEN_REFERENCE": None},
        {"DCP_JENKINS_TOKEN_REFERENCE": "a-literal-token"},  # a value, not a reference
        {"DCP_JENKINS_TOKEN_REFERENCE": "file:///etc/passwd"},
        {"DCP_JENKINS_URL": "http://user:pw@mock-jenkins:8080"},
        {"DCP_JENKINS_URL": "mock-jenkins:8080"},
        {"DCP_JENKINS_URL": "ftp://mock-jenkins"},
        {"DCP_JENKINS_USER": "has space"},
    ],
)
def test_an_unsafe_jenkins_configuration_does_not_start(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str | None]
) -> None:
    _jenkins(monkeypatch, **overrides)
    with pytest.raises(ConfigurationError):
        load_settings()


def test_a_jenkins_user_without_a_url_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_JENKINS_USER", "dcp")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_the_jenkins_error_does_not_echo_the_url_or_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _jenkins(monkeypatch, DCP_JENKINS_TOKEN_REFERENCE="canary-token-value-xyz")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "canary-token-value-xyz" not in str(caught.value)
