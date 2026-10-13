"""Secret redaction (docs/SECURITY.md §17, mandatory rule 7).

Two entry points:

* :func:`redact_text` — pattern-based scrubbing of a string.
* :func:`redact_value` — recursive scrubbing of JSON-shaped data, which also
  drops values whose *key* is sensitive regardless of the value's shape.

Honest limitation, repeated from the specification: this is best-effort. Regex
redaction will miss formats it has never seen. The primary control is not
emitting secrets at all; this is the net underneath that. The canary tests in
``tests/test_redaction.py`` are what give it any credibility at all.

The marker is fixed-width and carries no length, prefix or hash of the original,
so nothing here helps an attacker reconstruct a redacted value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Final

MARKER: Final = "[REDACTED]"

# Keys whose value is always dropped, whatever it looks like. Matched against the
# key with separators removed, so "api_key", "api-key" and "apiKey" all hit.
_SENSITIVE_KEY: Final = re.compile(
    r"(password|passwd|pwd|secret|token|apikey|accesskey|secretkey|privatekey"
    r"|authorization|credential|sessionid|cookie|clientsecret|connectionstring"
    r"|databaseurl|redisurl|dsn)",
    re.IGNORECASE,
)

_KEY_SEPARATORS: Final = re.compile(r"[_\-\s.]")

# Ordered: the most structurally specific patterns run first, so a PEM block is
# not first mangled by the generic key=value rule.
_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # PEM private key blocks
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MARKER,
    ),
    # JWT / JWS compact serialisation
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}"),
        MARKER,
    ),
    # URL userinfo: scheme://user:password@host
    (
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*)://([^:/\s@]+):([^@/\s]+)@"),
        rf"\1://\2:{MARKER}@",
    ),
    # Authorization header values
    (
        re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._\-+/=]{4,}", re.IGNORECASE),
        rf"\1 {MARKER}",
    ),
    # AWS access key identifiers
    (re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"), MARKER),
    # key=value / key: value forms, quoted or bare
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
            r"|secret[_-]?key|client[_-]?secret|authorization|auth[_-]?token"
            # The optional quote before the separator is what makes this match
            # serialised JSON (`"password": "..."`) and not just `password=...`.
            r"|session[_-]?id|credential)\b([\"']?\s*[:=]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;}&\]]+)"
        ),
        rf"\1\2{MARKER}",
    ),
)


def redact_text(text: str) -> str:
    """Scrub secret-shaped substrings from ``text``."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _key_is_sensitive(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(_KEY_SEPARATORS.sub("", key)))


def redact_value(value: Any, _depth: int = 0) -> Any:
    """Recursively scrub ``value``.

    Depth is capped so a cyclic or pathologically nested structure cannot turn
    logging into a denial of service.
    """
    if _depth > 12:
        return MARKER

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: MARKER if _key_is_sensitive(str(key)) else redact_value(item, _depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (bytes, bytearray)):
        return MARKER
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [redact_value(item, _depth + 1) for item in value]
    return value


def redaction_processor(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor applying :func:`redact_value` to the whole event."""
    return {
        key: MARKER if _key_is_sensitive(str(key)) else redact_value(item)
        for key, item in event_dict.items()
    }
