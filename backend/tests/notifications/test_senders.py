"""The two senders, with no network: HTTP through a mock transport, SMTP through a fake client."""

from __future__ import annotations

import smtplib
import ssl
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import pytest

from app.core.config import get_settings
from app.notifications import senders
from app.notifications.render import AlertMessage

PUBLIC = "93.184.216.34"


def message() -> AlertMessage:
    return AlertMessage(
        alert_id=uuid.UUID(int=1),
        rule_name="HostCpuCritical",
        severity="CRITICAL",
        status="FIRING",
        environment="production",
        summary="CPU high",
        target_type="server",
        target_id=uuid.UUID(int=2),
        first_seen_at=datetime(2026, 10, 19, tzinfo=UTC),
        link=None,
    )


@pytest.fixture(autouse=True)
def _allow_the_test_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "hooks.example.test,smtp.example.test")
    get_settings.cache_clear()


def http(status: int, **kwargs: Any) -> Any:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, **kwargs)

    outcome = senders.send_http(
        kwargs.pop("channel", "SLACK"),
        "https://hooks.example.test/services/T/B/x",
        message(),
        resolver=lambda h, p: [PUBLIC],
        transport=httpx.MockTransport(handler),
    )
    return outcome, seen


@pytest.mark.parametrize(
    ("status", "kind", "reason"),
    [
        (200, "sent", None),
        (404, "permanent", "http_404"),
        (302, "permanent", "redirect_refused"),
        (500, "transient", "http_500"),
        (429, "transient", "http_429"),
    ],
)
def test_http_outcomes(status: int, kind: str, reason: str | None) -> None:
    outcome, seen = http(status)
    assert (outcome.kind, outcome.reason) == (kind, reason)
    assert len(seen) == 1


def test_the_body_is_the_channels_own_shape() -> None:
    import json

    for channel, key in (("SLACK", "text"), ("TEAMS", "attachments"), ("WEBHOOK", "schema")):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request, seen: list[httpx.Request] = seen) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        senders.send_http(
            channel,
            "https://hooks.example.test/x",
            message(),
            resolver=lambda h, p: [PUBLIC],
            transport=httpx.MockTransport(handler),
        )
        assert key in json.loads(seen[0].content)


def test_a_private_destination_is_refused_and_nothing_is_sent() -> None:
    sent: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    outcome = senders.send_http(
        "SLACK",
        "https://hooks.example.test/x",
        message(),
        resolver=lambda h, p: ["10.0.0.5"],
        transport=httpx.MockTransport(handler),
    )
    assert (outcome.kind, outcome.reason) == ("permanent", "destination_refused")
    assert not sent


def test_a_url_off_the_allowlist_is_permanent_and_never_sent() -> None:
    sent: list[object] = []
    outcome = senders.send_http(
        "WEBHOOK",
        "https://evil.example.test/x",
        message(),
        resolver=lambda h, p: [PUBLIC],
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
    )
    assert (outcome.kind, outcome.reason) == ("permanent", "url_not_allowed")
    assert not sent


def test_transport_trouble_is_transient_and_carries_no_peer_text() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("canary-secret in the exception text")

    outcome = senders.send_http(
        "SLACK",
        "https://hooks.example.test/x",
        message(),
        resolver=lambda h, p: [PUBLIC],
        transport=httpx.MockTransport(boom),
    )
    assert (outcome.kind, outcome.reason) == ("transient", "connect_failed")
    assert "canary" not in repr(outcome)


# --- SMTP ----------------------------------------------------------------------------------


class FakeSmtp:
    """Records the calls; raises what the test tells it to."""

    instances: ClassVar[list[FakeSmtp]] = []
    fail_at: str | None = None
    fail_with: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._host = ""
        FakeSmtp.instances.append(self)

    def _maybe(self, step: str) -> None:
        if FakeSmtp.fail_at == step and FakeSmtp.fail_with is not None:
            raise FakeSmtp.fail_with

    def connect(self, host: str, port: int) -> None:
        self.calls.append(("connect", (host, port, self._host)))
        self._maybe("connect")

    def starttls(self, context: ssl.SSLContext) -> None:
        self.calls.append(("starttls", (self._host, context.verify_mode, context.check_hostname)))
        self._maybe("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", (user, password)))
        self._maybe("login")

    def send_message(self, mail: Any) -> None:
        self.calls.append(("send", (mail["To"], mail["From"])))
        self._maybe("send")

    def close(self) -> None:
        self.calls.append(("close", None))


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    FakeSmtp.instances = []
    FakeSmtp.fail_at = None
    FakeSmtp.fail_with = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    monkeypatch.setenv("DCP_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("DCP_SMTP_FROM", "dcp@example.test")
    get_settings.cache_clear()
    return FakeSmtp


def email() -> Any:
    return senders.send_email("ops@example.com", message(), resolver=lambda h, p: [PUBLIC])


def test_email_connects_to_the_checked_address_and_verifies_tls_for_the_name(
    fake_smtp: type[FakeSmtp],
) -> None:
    assert email().kind == "sent"
    calls = dict(fake_smtp.instances[0].calls)
    assert calls["connect"] == (PUBLIC, 587, "smtp.example.test")
    # STARTTLS with a verifying context, named for the host and not for the IP.
    assert calls["starttls"] == ("smtp.example.test", ssl.CERT_REQUIRED, True)
    assert calls["send"] == ("ops@example.com", "dcp@example.test")


def test_email_logs_in_only_when_credentials_are_set(
    fake_smtp: type[FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    email()
    assert "login" not in dict(fake_smtp.instances[-1].calls)
    monkeypatch.setenv("DCP_SMTP_USERNAME", "u")
    monkeypatch.setenv("DCP_SMTP_PASSWORD", "p")
    get_settings.cache_clear()
    email()
    assert dict(fake_smtp.instances[-1].calls)["login"] == ("u", "p")


def test_email_to_a_private_smtp_host_is_refused_before_any_connection(
    fake_smtp: type[FakeSmtp],
) -> None:
    outcome = senders.send_email("ops@example.com", message(), resolver=lambda h, p: ["10.0.0.9"])
    assert (outcome.kind, outcome.reason) == ("permanent", "destination_refused")
    assert not fake_smtp.instances


def test_email_without_smtp_configuration_is_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DCP_SMTP_HOST", raising=False)
    get_settings.cache_clear()
    outcome = senders.send_email("ops@example.com", message(), resolver=lambda h, p: [PUBLIC])
    assert (outcome.kind, outcome.reason) == ("permanent", "smtp_not_configured")


@pytest.mark.parametrize(
    ("step", "error", "kind", "reason"),
    [
        ("send", smtplib.SMTPResponseException(451, b"try later"), "transient", "smtp_busy"),
        ("send", smtplib.SMTPResponseException(550, b"no such user"), "permanent", "smtp_rejected"),
        ("send", smtplib.SMTPRecipientsRefused({}), "permanent", "smtp_rejected"),
        # A 4xx refusal of the recipient is "try later", and smtplib raises it as a refusal.
        (
            "send",
            smtplib.SMTPRecipientsRefused({"a@b.c": (451, b"try later")}),
            "transient",
            "smtp_busy",
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"no such user")}),
            "permanent",
            "smtp_rejected",
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"a@b.c": (451, b"x"), "d@e.f": (550, b"y")}),
            "permanent",
            "smtp_rejected",
        ),
        ("starttls", smtplib.SMTPNotSupportedError("no"), "permanent", "smtp_tls_unavailable"),
        ("starttls", ssl.SSLError("certificate verify failed"), "permanent", "tls_failed"),
        ("login", smtplib.SMTPAuthenticationError(535, b"bad"), "permanent", "smtp_auth_failed"),
        ("connect", TimeoutError(), "transient", "timeout"),
        ("connect", ConnectionRefusedError(), "transient", "connect_failed"),
        ("connect", smtplib.SMTPServerDisconnected("gone"), "transient", "connect_failed"),
    ],
)
def test_email_failure_classification(
    fake_smtp: type[FakeSmtp],
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    error: Exception,
    kind: str,
    reason: str,
) -> None:
    monkeypatch.setenv("DCP_SMTP_USERNAME", "u")
    monkeypatch.setenv("DCP_SMTP_PASSWORD", "p")
    get_settings.cache_clear()
    fake_smtp.fail_at, fake_smtp.fail_with = step, error
    outcome = email()
    assert (outcome.kind, outcome.reason) == (kind, reason)
    assert "close" in dict(fake_smtp.instances[0].calls)  # the connection is always closed


def test_email_without_starttls_skips_the_upgrade(
    fake_smtp: type[FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCP_SSRF_INSECURE_HOSTS", "smtp.example.test")
    monkeypatch.setenv("DCP_SMTP_STARTTLS", "false")
    get_settings.cache_clear()
    assert email().kind == "sent"
    assert "starttls" not in dict(fake_smtp.instances[0].calls)
