"""The text a channel receives (``OPEN-55``).

Everything is built from the **stored, already-redacted** alert row plus a fixed template.
The summary and rule text come from outside DCP (an alert's labels), so they are cleaned of
control characters, truncated, and escaped for the channel they are going to -- Slack
(``&``, ``<``, ``>`` -- which is what turns ``<!channel>`` and ``<url|text>`` into literal
text), Teams (Markdown specials), a generic webhook (JSON strings), and email (plain text
only, no HTML part, no header newline).

The Slack and Teams shapes are written from those products' public documentation and have
**not** been verified against a real workspace or tenant.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Final

SUMMARY_LIMIT: Final = 300
RULE_LIMIT: Final = 80
WEBHOOK_SCHEMA: Final = "dcp.alert.v1"


@dataclass(frozen=True, slots=True)
class AlertMessage:
    alert_id: uuid.UUID
    rule_name: str | None
    severity: str
    status: str
    environment: str | None
    summary: str
    target_type: str | None
    target_id: uuid.UUID | None
    first_seen_at: datetime
    #: ``DCP_PUBLIC_BASE_URL + /alerts/<id>``, or ``None`` when no base URL is configured.
    link: str | None


def clean(text: str | None, limit: int) -> str:
    """One line, no control or format characters, at most ``limit`` characters."""
    if not text:
        return ""
    flat = "".join(" " if unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} else c for c in text)
    flat = " ".join(flat.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def link_for(base_url: str | None, alert_id: uuid.UUID) -> str | None:
    return f"{base_url}/alerts/{alert_id}" if base_url else None


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_MARKDOWN_SPECIALS: Final = r"\`*_{}[]()#+-.!|~>"


def _markdown_escape(text: str) -> str:
    return "".join("\\" + c if c in _MARKDOWN_SPECIALS else c for c in text)


def _headline(message: AlertMessage) -> tuple[str, str]:
    where = clean(message.environment, 16) or "no environment"
    rule = clean(message.rule_name, RULE_LIMIT) or "alert"
    return f"[{message.severity}] {rule} ({where})", clean(message.summary, SUMMARY_LIMIT)


def slack_payload(message: AlertMessage) -> dict[str, Any]:
    head, summary = _headline(message)
    lines = [f"*{_slack_escape(head)}*", _slack_escape(summary)]
    if message.link:
        lines.append(f"<{message.link}|Open in DCP>")
    return {"text": "\n".join(line for line in lines if line)}


def teams_payload(message: AlertMessage) -> dict[str, Any]:
    head, summary = _headline(message)
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": _markdown_escape(head), "weight": "Bolder", "wrap": True},
        {"type": "TextBlock", "text": _markdown_escape(summary), "wrap": True},
    ]
    if message.link:
        body.append({"type": "TextBlock", "text": f"[Open in DCP]({message.link})", "wrap": True})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


def webhook_payload(message: AlertMessage) -> dict[str, Any]:
    return {
        "schema": WEBHOOK_SCHEMA,
        "alert_id": str(message.alert_id),
        "rule_name": clean(message.rule_name, RULE_LIMIT) or None,
        "severity": message.severity,
        "status": message.status,
        "environment": message.environment,
        "summary": clean(message.summary, SUMMARY_LIMIT),
        "target_type": message.target_type,
        "target_id": str(message.target_id) if message.target_id else None,
        "first_seen_at": message.first_seen_at.isoformat(),
        "link": message.link,
    }


def email_message(message: AlertMessage, *, sender: str, recipient: str) -> EmailMessage:
    head, summary = _headline(message)
    mail = EmailMessage()
    mail["Subject"] = f"[DCP] {head}"
    mail["From"] = sender
    mail["To"] = recipient
    lines = [head, "", summary, "", f"First seen: {message.first_seen_at.isoformat()}"]
    if message.link:
        lines += ["", f"Open: {message.link}"]
    mail.set_content("\n".join(lines))
    return mail
