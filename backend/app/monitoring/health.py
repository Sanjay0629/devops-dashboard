"""Host health from ``last_seen_at``: thresholds, the table of them, one pure function.

AD-14: health is computed from how long ago a host was last seen, against
per-environment thresholds (defaults: ``warning`` 2x the 15 s scrape interval,
``stale`` 5 min, ``unreachable`` 15 min). This module is only the arithmetic;
the sweep that persists it and the read path that shows it come later and both
call :func:`classify_health`.

**A state begins strictly after its threshold.** A host exactly on a line is the
oldest one still on the better side of it. This is the rule the existing
``stale`` flag and ``metrics.thresholds.is_stale`` already follow, so moving to
per-environment values does not shift a host across a line it was already on
the right side of. (``metrics.thresholds.classify`` crosses at ``>=``; that is
a value against a line an operator drew, not an age against a bound.)

**No evidence is not old evidence.** A host never seen, or a missing reference
time, is ``unknown`` — never ``stale`` or ``unreachable`` — and ``null`` is never
turned into an age (PRD §4.2, AD-14).

What this does not do: it does not look at utilisation (``OPEN-28``), at
``agent_status``, or at ``services``. ``healthy`` here means "collected on time",
and nothing wider.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.approvals.models import ENVIRONMENTS

#: Owned here, not in ``inventory.schemas``, because ``core.config`` imports this
#: module and the schemas import ``core.config`` (through ``core.ssrf``).
HealthState = Literal["healthy", "warning", "critical", "stale", "unreachable", "unknown"]

#: One day. A threshold beyond it is a typo for a value in minutes, and a host
#: that takes longer than a day to be called unreachable is not being monitored.
_MAX_SECONDS = 86_400


class HealthThresholds(BaseModel):
    """The three lines, in seconds of age. Strictly increasing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warning_after_seconds: int = Field(default=30, ge=1, le=_MAX_SECONDS)
    stale_after_seconds: int = Field(default=300, ge=1, le=_MAX_SECONDS)
    unreachable_after_seconds: int = Field(default=900, ge=1, le=_MAX_SECONDS)

    @model_validator(mode="after")
    def _ordered(self) -> HealthThresholds:
        # Equal lines would make a state unreachable (its range would be empty),
        # which is a configuration that looks set and does nothing.
        if not (
            self.warning_after_seconds < self.stale_after_seconds < self.unreachable_after_seconds
        ):
            raise ValueError(
                "must satisfy warning_after_seconds < stale_after_seconds "
                "< unreachable_after_seconds"
            )
        return self


class _Override(BaseModel):
    """Any subset of the three lines; what is absent is inherited."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warning_after_seconds: int | None = Field(default=None, ge=1, le=_MAX_SECONDS)
    stale_after_seconds: int | None = Field(default=None, ge=1, le=_MAX_SECONDS)
    unreachable_after_seconds: int | None = Field(default=None, ge=1, le=_MAX_SECONDS)


class HealthThresholdTable(BaseModel):
    """``DCP_HEALTH_THRESHOLDS``: a ``default`` and per-environment overrides.

    Precedence: AD-14 defaults, then ``default``, then the server's environment.
    The keys are exactly the ``servers.environment`` set (a test pins that), and
    ``extra="forbid"`` makes a typo like ``prod`` a startup failure rather than
    a setting that looks applied and is not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    default: _Override = _Override()
    development: _Override | None = None
    staging: _Override | None = None
    production: _Override | None = None
    other: _Override | None = None

    @model_validator(mode="after")
    def _every_resolution_is_valid(self) -> HealthThresholdTable:
        # Ordering is a property of the *merged* lines, which is what runs, so
        # check each resolution now: a bad override fails startup, not the sweep.
        for name in ENVIRONMENTS:
            self.for_environment(name)
        return self

    def for_environment(self, environment: str) -> HealthThresholds:
        """The lines for one environment; an unknown name gets the default."""
        merged: dict[str, int] = {}
        override = getattr(self, environment) if environment in ENVIRONMENTS else None
        for layer in (self.default, override):
            if layer is not None:
                merged.update(layer.model_dump(exclude_none=True))
        return HealthThresholds(**merged)


class HostHealth(NamedTuple):
    state: HealthState
    #: ``stale`` for both ``stale`` and ``unreachable``: a number was collected
    #: once and ``last_seen_at`` says when (API_CONTRACTS §4 rule 3), it is just
    #: too old to call current. ``unavailable`` means there is nothing to age.
    data_state: Literal["ok", "stale", "unavailable"]


_UNKNOWN = HostHealth("unknown", "unavailable")


def classify_health(
    thresholds: HealthThresholds,
    last_seen_at: datetime | None,
    reference: datetime | None,
) -> HostHealth:
    """Health of one host.

    ``reference`` is the instant the age is measured against: the last time
    Prometheus answered (D2), not the wall clock, so an outage stops the clock
    instead of ageing the fleet. ``None`` means that instant is not known.

    Both datetimes must be timezone-aware; mixing a naive one raises
    ``TypeError`` rather than guessing a zone.
    """
    if last_seen_at is None or reference is None:
        return _UNKNOWN
    # Clamped: two writers on two clocks can put last_seen_at a moment ahead.
    age = max((reference - last_seen_at).total_seconds(), 0.0)
    if age > thresholds.unreachable_after_seconds:
        return HostHealth("unreachable", "stale")
    if age > thresholds.stale_after_seconds:
        return HostHealth("stale", "stale")
    if age > thresholds.warning_after_seconds:
        return HostHealth("warning", "ok")
    return HostHealth("healthy", "ok")
