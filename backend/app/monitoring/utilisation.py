"""``DCP_UTILISATION_THRESHOLDS``: where the warning and critical lines for CPU, memory
and disk live (``OPEN-28``, ``OPEN-46``).

One home for the numbers. The metric catalogue
(:data:`app.metrics.templates.CATALOGUE`) holds the shipped defaults, this table holds
the *operator's* overrides, and the Prometheus rule file is **generated** from the two
(``scripts/gen_alert_rules.py``), so an alert and the chart beside it can never use
different numbers, and there is never a second hand-written copy to drift.

Shaped like :class:`app.monitoring.health.HealthThresholdTable` on purpose: a
``default`` and the four ``servers.environment`` values, ``extra="forbid"`` so a typo
such as ``prod`` fails startup instead of looking applied, and every resolution is
checked now (``warning < critical``) rather than when a rule is generated.

Keys are template ids (``cpu_utilisation``, ``memory_utilisation``,
``disk_utilisation``), the one vocabulary the metrics API already uses.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.approvals.models import ENVIRONMENTS

#: The templates that carry a threshold in the catalogue, and so the only ones an
#: override may name. Asserted against the catalogue in a test.
THRESHOLD_METRICS: Final[tuple[str, ...]] = (
    "cpu_utilisation",
    "memory_utilisation",
    "disk_utilisation",
)


class UtilisationLine(BaseModel):
    """The two lines for one metric, in percent. ``warning < critical``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warning: float = Field(gt=0, lt=100)
    critical: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> UtilisationLine:
        if not self.warning < self.critical:
            raise ValueError("must satisfy warning < critical")
        return self


class _LineOverride(BaseModel):
    """Either or both lines; what is absent is inherited."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warning: float | None = Field(default=None, gt=0, lt=100)
    critical: float | None = Field(default=None, gt=0, le=100)


class _Override(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_utilisation: _LineOverride | None = None
    memory_utilisation: _LineOverride | None = None
    disk_utilisation: _LineOverride | None = None


def catalogue_defaults() -> dict[str, dict[str, float]]:
    """The shipped lines, from the metric catalogue (imported late: see below)."""
    # Imported here, not at module level: ``core.config`` imports this module and
    # ``metrics.templates`` imports ``inventory.schemas``, which imports
    # ``core.config``. By the time anyone calls this, every module is loaded.
    from app.metrics.templates import BY_ID

    defaults: dict[str, dict[str, float]] = {}
    for metric in THRESHOLD_METRICS:
        lines = BY_ID[metric].default_thresholds
        if lines is None:  # pragma: no cover - a catalogue edit that broke the contract
            raise ValueError(f"template {metric!r} has no default thresholds")
        defaults[metric] = {
            "warning": float(lines["warning"]),
            "critical": float(lines["critical"]),
        }
    return defaults


class UtilisationThresholdTable(BaseModel):
    """Catalogue defaults, then ``default``, then the server's environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default: _Override = _Override()
    development: _Override | None = None
    staging: _Override | None = None
    production: _Override | None = None
    other: _Override | None = None

    @model_validator(mode="after")
    def _every_resolution_is_valid(self) -> UtilisationThresholdTable:
        # Ordering is a property of the *merged* lines, which is what runs; check
        # each resolution now so a bad override fails startup, not generation.
        for name in ENVIRONMENTS:
            self.for_environment(name)
        return self

    def for_environment(self, environment: str) -> dict[str, UtilisationLine]:
        """The lines for one environment; an unknown name gets the default."""
        merged = catalogue_defaults()
        override = getattr(self, environment) if environment in ENVIRONMENTS else None
        for layer in (self.default, override):
            if layer is None:
                continue
            for metric in THRESHOLD_METRICS:
                part = getattr(layer, metric)
                if part is not None:
                    merged[metric].update(part.model_dump(exclude_none=True))
        return {metric: UtilisationLine(**lines) for metric, lines in merged.items()}
