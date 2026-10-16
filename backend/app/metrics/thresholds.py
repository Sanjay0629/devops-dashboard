"""Threshold classification and the staleness bound for a metric reading.

Two pure functions and one constant, kept apart from the Prometheus plumbing
so the boundary cases can be tested without a transport.

**A threshold is crossed at ``>=``.** A value exactly on ``warning`` is a
warning: the number is a line the operator drew, and a host sitting on it is on
the wrong side of it. The same value on ``critical`` is critical.

**No value is not a normal value.** ``None`` classifies to ``None``, never
``"normal"`` — an unreadable host is unknown, not well (PRD §4.2). The same
goes for a template with no threshold: ``None`` is "nothing was drawn", not
"zero", and the catalogue leaves seven of the ten without one on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

ThresholdLevel = Literal["normal", "warning", "critical"]

#: Age of a host's latest scrape beyond which a reading is ``stale`` rather
#: than ``ok``. Deliberately **below** Prometheus's 5-minute lookback: an
#: instant query stops returning a series once its last sample is older than
#: that, so a bound at or above it could never be reached — the reading would
#: vanish (``unavailable``) first. Four scrape intervals at the stack's 15 s.
#:
#: A module constant rather than a setting for the reason the circuit-breaker
#: thresholds are (PROGRESS issue 54): a setting needs a ``.env.example`` entry
#: and a Compose passthrough to have any effect, and nothing has asked to tune
#: it. It is *not* ``server_stale_after_seconds``, which is the host-level
#: bound the M2.5 sweep will own (AD-14); this is "how old may a number be
#: before we stop calling it current".
STALE_AFTER_SECONDS: Final = 60


def classify(thresholds: Mapping[str, float] | None, value: float | None) -> ThresholdLevel | None:
    """Where ``value`` sits against ``thresholds``, or ``None`` if it cannot."""
    if thresholds is None or value is None:
        return None
    if value >= thresholds["critical"]:
        return "critical"
    if value >= thresholds["warning"]:
        return "warning"
    return "normal"


def is_stale(age_seconds: float) -> bool:
    """Is a scrape this old too old to present as current?

    Strictly greater: a sample exactly at the bound is the oldest still
    counted as current.
    """
    return age_seconds > STALE_AFTER_SECONDS
