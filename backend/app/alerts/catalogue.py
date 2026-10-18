"""The closed set of alert rules DCP produces, and what each one means (``OPEN-40/41/46``).

One catalogue, two readers. ``rulegen`` turns it into the Prometheus rule file;
the webhook ingestion reads it back to decide what an incoming alert *is*. That is
deliberate: **everything that gives an alert its meaning comes from here and not from
the payload.** The payload is untrusted text (SECURITY.md §21), so the severity of a
row, the discriminators that make up its dedup key and whether it is accepted at all
are looked up by rule name, never taken from a label or an annotation. A payload
naming a rule that is not here is rejected, not interpreted.

Severity is part of the rule name (``HostCpuWarning`` / ``HostCpuCritical``) and the
two generated expressions are mutually exclusive, so one condition is never two open
rows and a warning-to-critical move is a resolved row and a new one (``OPEN-41``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Severity = Literal["WARNING", "CRITICAL"]

#: Always firing. Routed to the webhook only so DCP can tell "the pipeline is alive and
#: nothing is wrong" from "the pipeline is dead" (``OPEN-47``). It is never stored.
WATCHDOG_RULE: Final = "DcpWatchdog"


@dataclass(frozen=True, slots=True)
class AlertRule:
    name: str
    #: ``host_not_reporting``, ``cpu``, ``memory``, ``disk`` or ``unit_failed``.
    condition: str
    severity: Severity
    #: Prometheus labels that distinguish two alerts of this rule on one target
    #: (a failed unit is per unit). Any *other* label can never multiply rows.
    discriminators: tuple[str, ...] = ()
    #: How long the condition must hold before it fires. Working defaults, not
    #: measurements (``OPEN-46``); ``host_not_reporting`` is instead taken per
    #: environment from ``DCP_HEALTH_THRESHOLDS``.
    for_seconds: int | None = None


#: ``for:`` durations, named so the generator and its tests agree on one number.
CPU_FOR_SECONDS: Final = 600
MEMORY_FOR_SECONDS: Final = 300
DISK_FOR_SECONDS: Final = 300
UNIT_FAILED_FOR_SECONDS: Final = 60

RULES: Final[tuple[AlertRule, ...]] = (
    AlertRule("HostNotReporting", "host_not_reporting", "CRITICAL"),
    AlertRule("HostCpuWarning", "cpu", "WARNING", for_seconds=CPU_FOR_SECONDS),
    AlertRule("HostCpuCritical", "cpu", "CRITICAL", for_seconds=CPU_FOR_SECONDS),
    AlertRule("HostMemoryWarning", "memory", "WARNING", for_seconds=MEMORY_FOR_SECONDS),
    AlertRule("HostMemoryCritical", "memory", "CRITICAL", for_seconds=MEMORY_FOR_SECONDS),
    AlertRule("HostDiskWarning", "disk", "WARNING", for_seconds=DISK_FOR_SECONDS),
    AlertRule("HostDiskCritical", "disk", "CRITICAL", for_seconds=DISK_FOR_SECONDS),
    # ``name`` is the unit's name label on node_exporter's systemd collector.
    AlertRule(
        "SystemdUnitFailed",
        "unit_failed",
        "CRITICAL",
        discriminators=("name",),
        for_seconds=UNIT_FAILED_FOR_SECONDS,
    ),
)

BY_NAME: Final[dict[str, AlertRule]] = {rule.name: rule for rule in RULES}

#: DCP's own self-monitoring (``source = DCP_SYSTEM``, ``OPEN-40``), raised by the
#: maintenance task rather than by Prometheus. Deliberately **not** in :data:`RULES`:
#: the webhook only accepts the Prometheus catalogue, so no payload can forge one of these.
SYSTEM_RULES: Final[tuple[AlertRule, ...]] = (
    AlertRule("PartitionRunwayLow", "partition_runway", "WARNING", discriminators=("table",)),
    AlertRule(
        "PartitionRunwayExhausted", "partition_runway", "CRITICAL", discriminators=("table",)
    ),
)

#: PRD §4.8 conditions DCP has no producer for yet (``OPEN-40``). Listed so the UI and
#: the status row can say so instead of implying they are monitored.
NOT_PRODUCED_YET: Final[tuple[tuple[str, str], ...]] = (
    ("application_health_check_failed", "M5"),
    ("deployment_failed", "M5"),
    ("operation_timed_out_or_failed", "M4"),
)
