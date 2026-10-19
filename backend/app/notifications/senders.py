"""The two ways a message leaves DCP: an HTTPS POST and an SMTP submission.

``OPEN-49`` and ``OPEN-54``.

Both return an :class:`~app.notifications.outcome.Outcome` and **never raise for a delivery
problem**: a refusal, a timeout, a bad status and a rejected recipient are all facts the
state machine decides what to do with. Neither returns, logs or stores the destination, the
peer's response body or its error text -- only a closed reason code.

Every connection goes through the checked-address path of ``core/ssrf.py``; the SMTP host is
treated like any other destination (it must be on the allowlist, and is resolved and checked).

``app/alerts`` must never import this module (SECURITY.md §21); a test pins that.
"""

from __future__ import annotations

import contextlib
import smtplib
import ssl
from typing import Final

import httpx

from app.core.config import get_settings
from app.core.ssrf import (
    REQUEST_TIMEOUT_SECONDS,
    OutboundError,
    Resolver,
    UrlNotAllowedError,
    post_json,
    resolve_checked,
)
from app.notifications import render
from app.notifications.outcome import SENT, Outcome, classify_http, classify_reason

SMTP_TIMEOUT_SECONDS: Final = REQUEST_TIMEOUT_SECONDS

_PAYLOADS: Final = {
    "SLACK": render.slack_payload,
    "TEAMS": render.teams_payload,
    "WEBHOOK": render.webhook_payload,
}


def send_http(
    channel_type: str,
    url: str,
    message: render.AlertMessage,
    *,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Outcome:
    body = _PAYLOADS[channel_type](message)
    try:
        result = post_json(url, body, resolver=resolver, transport=transport)
    except UrlNotAllowedError:
        return classify_reason("url_not_allowed")
    except OutboundError as error:
        return classify_reason(error.reason)
    return classify_http(result.status, result.retry_after)


def send_email(
    recipient: str, message: render.AlertMessage, *, resolver: Resolver | None = None
) -> Outcome:
    settings = get_settings()
    host = settings.smtp_host
    sender = settings.smtp_from
    if host is None or sender is None:
        return classify_reason("smtp_not_configured")
    mail = render.email_message(message, sender=sender, recipient=recipient)
    try:
        address = resolve_checked(host, settings.smtp_port, resolver)
    except OutboundError as error:
        return classify_reason(error.reason)

    smtp = smtplib.SMTP(timeout=SMTP_TIMEOUT_SECONDS)
    # Connect to the address that was checked, but let STARTTLS verify the certificate for the
    # *name*: smtplib hands ``_host`` to ``wrap_socket`` as ``server_hostname``.
    smtp._host = host  # type: ignore[attr-defined]  # no public way to set the TLS name
    try:
        smtp.connect(address, settings.smtp_port)
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(mail)
    except smtplib.SMTPNotSupportedError:
        return classify_reason("smtp_tls_unavailable")
    except smtplib.SMTPAuthenticationError:
        return classify_reason("smtp_auth_failed")
    except smtplib.SMTPResponseException as error:
        return (
            Outcome("transient", "smtp_busy")
            if 400 <= error.smtp_code < 500
            else Outcome("permanent", "smtp_rejected")
        )
    except smtplib.SMTPRecipientsRefused:
        return classify_reason("smtp_rejected")
    except ssl.SSLError:
        return classify_reason("tls_failed")
    except TimeoutError:
        return classify_reason("timeout")
    except (OSError, smtplib.SMTPException):
        return classify_reason("connect_failed")
    finally:
        with contextlib.suppress(OSError):
            smtp.close()
    return SENT
