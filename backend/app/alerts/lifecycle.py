"""Alert statuses, which moves between them are legal, and the dedup key.

Pure, so the rules the integration tests then exercise against a database are first
pinned here without one.

**Acknowledgement never implies resolution** (PRD §4.8): ``ACKNOWLEDGED`` is an *open*
status. Only the source resolves an alert, and an alert that was never confirmed either
way ends ``EXPIRED``, not ``RESOLVED`` (``OPEN-44``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Final

STATUSES: Final[tuple[str, ...]] = ("FIRING", "ACKNOWLEDGED", "RESOLVED", "EXPIRED")
#: An alert is *open* while its problem is, as far as DCP knows, still there.
OPEN_STATUSES: Final[tuple[str, ...]] = ("FIRING", "ACKNOWLEDGED")
SEVERITIES: Final[tuple[str, ...]] = ("INFO", "WARNING", "CRITICAL")
SOURCES: Final[tuple[str, ...]] = (
    "PROMETHEUS",
    "DCP_HEALTH_CHECK",
    "DCP_OPERATION",
    "DCP_DEPLOYMENT",
    "DCP_SYSTEM",
    "MANUAL",
)

#: ``(from, to)`` pairs that exist. Everything else is illegal, so a new status or a new
#: path has to be added here on purpose.
_LEGAL: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("FIRING", "ACKNOWLEDGED"),  # a person acknowledges
        ("FIRING", "RESOLVED"),  # the source says it cleared
        ("ACKNOWLEDGED", "RESOLVED"),
        ("FIRING", "EXPIRED"),  # nothing heard for too long
        ("ACKNOWLEDGED", "EXPIRED"),
    }
)

#: Open alerts not refreshed for this long are ``EXPIRED``: three times Alertmanager's
#: ``repeat_interval`` (``infra/alertmanager/alertmanager.yml``), so one lost repeat does
#: not expire a live alert. A constant, like the circuit-breaker thresholds.
REPEAT_INTERVAL_SECONDS: Final = 3_600
EXPIRE_AFTER_SECONDS: Final = 3 * REPEAT_INTERVAL_SECONDS


def can_transition(current: str, new: str) -> bool:
    return (current, new) in _LEGAL


def is_open(status: str) -> bool:
    return status in OPEN_STATUSES


def dedup_key(
    *,
    source: str,
    rule_name: str,
    target: str,
    discriminators: Mapping[str, str],
) -> str:
    """The identity of one condition on one target (``OPEN-41``).

    ``target`` is the resolved server id, or ``"unresolved:<instance>"`` for an alert
    whose labels matched nothing (so unrelated unmatched alerts of one rule do not
    collapse into a single row). The key is **DCP's own**: Alertmanager's fingerprint
    hashes every label, so a label that merely varies would open a new row.

    JSON, not a delimiter: a label value is untrusted text and could contain any
    separator, which would let two different tuples encode identically. Discriminators
    are sorted by name so the key does not depend on dictionary order.
    """
    encoded = json.dumps(
        [source, rule_name, target, sorted(discriminators.items())],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()
