"""The alert webhook settings fail closed (OPEN-43)."""

from __future__ import annotations

import pytest

from app.core.config import ConfigurationError, load_settings


def test_unset_means_no_secret_and_no_address_is_allowed() -> None:
    settings = load_settings()
    assert settings.alert_webhook_secret is None
    assert settings.alert_webhook_allowed_cidrs == ()
    assert not settings.alertmanager_configured


def test_the_secret_must_be_long_and_is_not_printable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_ALERT_WEBHOOK_SECRET", "short")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "short" not in str(caught.value)
    secret = "s" * 40
    monkeypatch.setenv("DCP_ALERT_WEBHOOK_SECRET", secret)
    settings = load_settings()
    assert secret not in repr(settings) and secret not in str(settings.alert_webhook_secret)


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "::/0", "not-a-network", "10.0.0.0/33"])
def test_a_bad_or_all_addresses_cidr_is_refused(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("DCP_ALERT_WEBHOOK_ALLOWED_CIDRS", f"10.0.0.0/8,{bad}")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_cidrs_split_from_a_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_ALERT_WEBHOOK_ALLOWED_CIDRS", "10.0.0.0/8, 172.16.0.0/12 ,,")
    assert load_settings().alert_webhook_allowed_cidrs == ("10.0.0.0/8", "172.16.0.0/12")


def test_alertmanager_url_is_https_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_ALERTMANAGER_URL", "http://alertmanager:9093/")
    settings = load_settings()
    assert (
        settings.alertmanager_url == "http://alertmanager:9093" and settings.alertmanager_configured
    )
    monkeypatch.setenv("DCP_ENVIRONMENT", "production")
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    with pytest.raises(ConfigurationError):
        load_settings()
    monkeypatch.setenv("DCP_ALERTMANAGER_URL", "http://user:pw@alertmanager:9093")
    monkeypatch.setenv("DCP_ENVIRONMENT", "local")
    with pytest.raises(ConfigurationError):
        load_settings()
