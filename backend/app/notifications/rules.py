"""Which alerts a channel hears about (``OPEN-52``).

``routing_rules`` is one JSON object with three optional dimensions. **An absent or empty
dimension restricts nothing; ``{}`` is a catch-all** (it is also the column default). The
inputs to a match are the alert row's own severity, environment and target type -- never a
payload's claim -- and an alert with **no resolved environment** (unlinked, or a
``DCP_SYSTEM`` alert) does not match a channel that restricts environment: the fail-closed
stance of ``OPEN-45``, so an alert cannot place itself in an environment's channel.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["INFO", "WARNING", "CRITICAL"]
Environment = Literal["development", "staging", "production", "other"]
TargetType = Literal["server", "service"]


class RoutingRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: list[Severity] = Field(default_factory=list, max_length=3)
    environment: list[Environment] = Field(default_factory=list, max_length=4)
    target_type: list[TargetType] = Field(default_factory=list, max_length=2)

    def matches(self, *, severity: str, environment: str | None, target_type: str | None) -> bool:
        return (
            _allows(self.severity, severity)
            and _allows(self.environment, environment)
            and _allows(self.target_type, target_type)
        )


def _allows(restriction: Sequence[str], value: str | None) -> bool:
    """No restriction allows anything -- including no value; a restriction needs a match."""
    return not restriction or (value is not None and value in restriction)


def parse_stored_rules(raw: object) -> RoutingRules | None:
    """The rules in a stored row, or ``None`` if they do not validate.

    ``None`` means *do not deliver*: a row somebody edited by hand into something this code
    does not understand is not read as a catch-all.
    """
    try:
        return RoutingRules.model_validate(raw)
    except ValueError:
        return None
