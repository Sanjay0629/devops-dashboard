"""The pure parts of M3.4: routing, outcome and schedule, rendering, addresses, settings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import ConfigurationError, load_settings
from app.notifications import render
from app.notifications.address import MailboxError, parse_mailbox
from app.notifications.destination import (
    INVALID,
    SECRET_MISPLACED,
    DestinationError,
    validate_destination,
)
from app.notifications.outcome import (
    MAX_RETRY_AFTER_SECONDS,
    classify_http,
    classify_reason,
    max_attempts,
    next_delay,
)
from app.notifications.rules import RoutingRules, parse_stored_rules

# --- routing ---------------------------------------------------------------------------------


def _match(rules: dict[str, object], **alert: str | None) -> bool:
    values = {"severity": "WARNING", "environment": "production", "target_type": "server"}
    values.update(alert)
    return RoutingRules.model_validate(rules).matches(**values)  # type: ignore[arg-type]


def test_an_empty_rule_set_is_a_catch_all() -> None:
    assert _match({})
    assert _match({}, environment=None, target_type=None)  # even an unlinked alert


@pytest.mark.parametrize(
    ("rules", "alert", "expected"),
    [
        ({"severity": ["CRITICAL"]}, {"severity": "CRITICAL"}, True),
        ({"severity": ["CRITICAL"]}, {"severity": "WARNING"}, False),
        ({"severity": ["WARNING", "CRITICAL"]}, {"severity": "WARNING"}, True),
        ({"environment": ["production"]}, {"environment": "production"}, True),
        ({"environment": ["production"]}, {"environment": "staging"}, False),
        ({"target_type": ["service"]}, {"target_type": "server"}, False),
        ({"target_type": ["service"]}, {"target_type": "service"}, True),
        # Dimensions are ANDed.
        (
            {"severity": ["CRITICAL"], "environment": ["production"]},
            {"severity": "CRITICAL", "environment": "staging"},
            False,
        ),
        ({"severity": []}, {"severity": "INFO"}, True),  # an empty list restricts nothing
    ],
)
def test_routing_matrix(rules: dict[str, object], alert: dict[str, str], expected: bool) -> None:
    assert _match(rules, **alert) is expected


def test_a_restricted_dimension_never_matches_an_alert_with_no_value() -> None:
    """OPEN-45's stance: an unlinked alert cannot place itself in an environment's channel."""
    assert not _match({"environment": ["production"]}, environment=None)
    assert not _match({"target_type": ["server"]}, target_type=None)
    assert _match({"severity": ["WARNING"]}, environment=None)


@pytest.mark.parametrize(
    "bad",
    [
        {"unknown": 1},
        {"severity": ["URGENT"]},
        {"environment": ["moon"]},
        {"target_type": ["application"]},
        {"severity": "CRITICAL"},
    ],
)
def test_rules_are_a_closed_schema(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RoutingRules.model_validate(bad)
    assert parse_stored_rules(bad) is None  # a hand-edited row is "do not deliver", not "all"


# --- outcome and schedule --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind", "reason"),
    [
        (200, "sent", None),
        (204, "sent", None),
        (299, "sent", None),
        (301, "permanent", "redirect_refused"),
        (302, "permanent", "redirect_refused"),
        (400, "permanent", "http_400"),
        (401, "permanent", "http_401"),
        (403, "permanent", "http_403"),
        (404, "permanent", "http_404"),
        (410, "permanent", "http_410"),
        (408, "transient", "http_408"),
        (429, "transient", "http_429"),
        (500, "transient", "http_500"),
        (503, "transient", "http_503"),
    ],
)
def test_http_classification(status: int, kind: str, reason: str | None) -> None:
    outcome = classify_http(status)
    assert (outcome.kind, outcome.reason) == (kind, reason)


def test_transport_reasons() -> None:
    for transient in ("timeout", "connect_failed", "dns_failed"):
        assert classify_reason(transient).kind == "transient"
    for permanent in ("destination_refused", "secret_unresolvable", "url_not_allowed", "x"):
        assert classify_reason(permanent).kind == "permanent"


DELAYS = (60, 300, 900, 3600)


def test_the_schedule_and_the_cap() -> None:
    assert max_attempts(DELAYS) == 5
    assert [next_delay(DELAYS, n) for n in (1, 2, 3, 4)] == [60, 300, 900, 3600]
    assert next_delay(DELAYS, 5) is None  # the fifth failure abandons
    assert next_delay(DELAYS, 6) is None
    assert next_delay(DELAYS, 0) is None  # never "failed" before an attempt was made


def test_retry_after_lengthens_but_never_shortens_and_is_capped() -> None:
    assert next_delay(DELAYS, 1, retry_after=5) == 60
    assert next_delay(DELAYS, 1, retry_after=600) == 600
    assert next_delay(DELAYS, 1, retry_after=10**9) == MAX_RETRY_AFTER_SECONDS


# --- addresses and destinations --------------------------------------------------------------


@pytest.mark.parametrize("value", ["ops@example.com", "a.b+tag@mail.example.org", "x@y.io"])
def test_plain_addresses(value: str) -> None:
    assert parse_mailbox(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "ops@example.com\n",
        "ops@example.com\r\nBcc: attacker@example.net",
        "ops@example.com, other@example.com",
        "Ops <ops@example.com>",
        '"ops"@example.com',
        "ops@example.com ",
        " ops@example.com",
        "ops@localhost",
        "ops@@example.com",
        "a..b@example.com",
        ".a@example.com",
        "ops@example..com",
        "",
        "a" * 250 + "@example.com",
    ],
)
def test_addresses_that_are_not_single_and_plain(value: str) -> None:
    with pytest.raises(MailboxError):
        parse_mailbox(value)


def test_destination_validation_by_type() -> None:
    assert validate_destination("EMAIL", "ops@example.com") == "ops@example.com"
    for kind in ("SLACK", "TEAMS", "WEBHOOK"):
        assert validate_destination(kind, "env://OPS_HOOK") == "env://OPS_HOOK"


@pytest.mark.parametrize("kind", ["SLACK", "TEAMS", "WEBHOOK", "EMAIL"])
def test_a_url_is_reported_as_misplaced_and_never_echoed(kind: str) -> None:
    secret = "https://hooks.example.test/services/T0/B0/canary-secret"
    with pytest.raises(DestinationError) as caught:
        validate_destination(kind, secret)
    assert caught.value.args[0] == SECRET_MISPLACED
    assert "canary-secret" not in repr(caught.value)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("EMAIL", "env://OPS"),
        ("SLACK", "ops@example.com"),
        ("SLACK", "lowercase"),
        ("PAGER", "env://X"),
        ("WEBHOOK", ""),
    ],
)
def test_other_malformed_destinations(kind: str, value: str) -> None:
    with pytest.raises(DestinationError) as caught:
        validate_destination(kind, value)
    assert caught.value.args[0] == INVALID


# --- rendering -------------------------------------------------------------------------------

HOSTILE = "<!channel> <https://evil.example.test|click> & *bold* [x](https://evil) \r\nBcc: x"


def _message(summary: str = "CPU high", link: str | None = "https://dcp.example.test/alerts/1"):
    return render.AlertMessage(
        alert_id=uuid.UUID(int=1),
        rule_name="HostCpuCritical",
        severity="CRITICAL",
        status="FIRING",
        environment="production",
        summary=summary,
        target_type="server",
        target_id=uuid.UUID(int=2),
        first_seen_at=datetime(2026, 10, 19, tzinfo=UTC),
        link=link,
    )


def test_slack_neutralises_mentions_and_links_from_untrusted_text() -> None:
    text = render.slack_payload(_message(HOSTILE))["text"]
    assert "<!channel>" not in text and "<https://evil" not in text
    assert "&lt;!channel&gt;" in text
    assert "<https://dcp.example.test/alerts/1|Open in DCP>" in text  # DCP's own link survives
    assert "\r" not in text


def test_teams_escapes_markdown_from_untrusted_text() -> None:
    card = render.teams_payload(_message(HOSTILE))["attachments"][0]["content"]
    flat = " ".join(block["text"] for block in card["body"][:2])
    assert "[x](https://evil)" not in flat and "\\[x\\]\\(https://evil\\)" in flat


def test_the_generic_webhook_has_a_fixed_schema() -> None:
    payload = render.webhook_payload(_message(HOSTILE))
    assert set(payload) == {
        "schema",
        "alert_id",
        "rule_name",
        "severity",
        "status",
        "environment",
        "summary",
        "target_type",
        "target_id",
        "first_seen_at",
        "link",
    }
    assert payload["schema"] == "dcp.alert.v1"
    assert "\r" not in payload["summary"] and "\n" not in payload["summary"]


def test_email_cannot_grow_a_header_and_has_no_html_part() -> None:
    mail = render.email_message(
        _message(HOSTILE), sender="dcp@example.com", recipient="ops@example.com"
    )
    assert mail["Bcc"] is None
    assert mail.get_content_type() == "text/plain" and not mail.is_multipart()
    assert "\n" not in str(mail["Subject"]).strip()


def test_summaries_are_truncated_and_stripped_of_control_characters() -> None:
    assert len(render.clean("x" * 5000, render.SUMMARY_LIMIT)) == render.SUMMARY_LIMIT
    assert render.clean("a\x00b\x1b[31mc‮d", 50) == "a b [31mc d"
    assert render.clean(None, 10) == ""


def test_no_link_when_no_base_url() -> None:
    assert render.link_for(None, uuid.UUID(int=1)) is None
    assert "Open in DCP" not in render.slack_payload(_message(link=None))["text"]
    assert render.link_for("https://dcp.example.test", uuid.UUID(int=1)).endswith(
        "/alerts/00000000-0000-0000-0000-000000000001"
    )  # type: ignore[union-attr]


# --- settings --------------------------------------------------------------------------------


def test_notification_settings_default_safe() -> None:
    settings = load_settings()
    assert settings.notification_retry_delays_seconds == (60, 300, 900, 3600)
    assert not settings.smtp_configured
    assert settings.ssrf_allowed_networks == ()
    assert settings.public_base_url is None


@pytest.mark.parametrize("bad", ["", "0", "86401", "1,x", ",".join(["5"] * 9)])
def test_bad_retry_delays_are_refused(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("DCP_NOTIFICATION_RETRY_DELAYS_SECONDS", bad)
    with pytest.raises(ConfigurationError):
        load_settings()


def test_retry_delays_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_NOTIFICATION_RETRY_DELAYS_SECONDS", "2, 4")
    assert load_settings().notification_retry_delays_seconds == (2, 4)


def test_smtp_settings_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_SMTP_HOST", "smtp.example.test")
    with pytest.raises(ConfigurationError):  # not on the SSRF host allowlist
        load_settings()
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "smtp.example.test")
    with pytest.raises(ConfigurationError):  # no sender
        load_settings()
    monkeypatch.setenv("DCP_SMTP_FROM", "dcp@example.test")
    settings = load_settings()
    assert settings.smtp_configured and settings.smtp_starttls
    monkeypatch.setenv("DCP_SMTP_STARTTLS", "false")
    with pytest.raises(ConfigurationError):  # clear text only for an explicitly insecure host
        load_settings()
    monkeypatch.setenv("DCP_SSRF_INSECURE_HOSTS", "smtp.example.test")
    assert not load_settings().smtp_starttls
    monkeypatch.setenv("DCP_SMTP_USERNAME", "u")
    with pytest.raises(ConfigurationError):  # username without password
        load_settings()
    monkeypatch.setenv("DCP_SMTP_PASSWORD", "canary-smtp-password")
    settings = load_settings()
    assert "canary-smtp-password" not in repr(settings)


@pytest.mark.parametrize("bad", ["ops@example.com\n", "A <a@example.com>", "nope"])
def test_a_bad_sender_is_refused(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("DCP_SMTP_FROM", bad)
    with pytest.raises(ConfigurationError):
        load_settings()


def test_blank_optional_settings_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose and .env.example pass an unset variable as the empty string."""
    for name in (
        "DCP_PUBLIC_BASE_URL",
        "DCP_SMTP_HOST",
        "DCP_SMTP_FROM",
        "DCP_SMTP_USERNAME",
        "DCP_SMTP_PASSWORD",
        "DCP_SSRF_ALLOWED_NETWORKS",
    ):
        monkeypatch.setenv(name, "")
    settings = load_settings()
    assert not settings.smtp_configured and settings.public_base_url is None
