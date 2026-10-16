"""The allowlisted metric template catalogue (AD-04, docs/API_CONTRACTS.md §15).

**No PromQL ever arrives from a client.** A caller names a template id; this
module owns the query string. That single decision closes PromQL injection and
cardinality abuse together (AD-04), and it is why the catalogue is a frozen
module-level constant rather than anything a request, a database row or a
provider response can extend.

Three things the renderer is careful about, each a real defect otherwise:

**The selector is built, never concatenated from a request.** The only
caller-influenced value that reaches a query is ``servers.metrics_instance``,
and it is re-validated against
:data:`app.inventory.schemas.METRICS_INSTANCE_PATTERN` *here* — not trusted
because §14.2 validated it on the way in. A row can also arrive from a seed, a
migration or a direct ``UPDATE``, and fail-closed (mandatory rule 6) means the
renderer refuses rather than escaping something unexpected and hoping.

**Escaping happens in two layers and in one order.** A value destined for
``instance=~"…"`` is regex-escaped first and then escaped for the PromQL
string literal, because the regex escape *introduces* backslashes that the
string literal must then carry. Doing it the other way round emits
``"node\\-exporter"``, which is not a valid PromQL escape sequence, and doing
only one of the two lets ``.`` in a hostname match any character — which
silently attributes another host's metrics to this server.

**Per-OS-family, with no parity assumption.** LINUX templates target
``node_exporter`` and are verified against a real one. WINDOWS has **no
queries at all**: ``windows_exporter`` cannot run in a Linux container, so
shipping Windows PromQL would mean shipping strings that have never been
executed against anything. A WINDOWS server therefore resolves to the
``unsupported`` data state — a distinct, honest answer — rather than an empty
chart or a Linux query pointed at a Windows host (CLAUDE.md §5, PRD §4.4).
``OPEN-09`` stays open with exactly this as its recorded default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from string import Template
from typing import Final

from app.inventory.schemas import METRICS_INSTANCE_PATTERN

#: The Prometheus ``job`` label the host exporter is scraped under, matching
#: ``infra/prometheus/prometheus.yml``. Pinned to one name on purpose: without
#: it, ``up{instance="…"}`` would also match DCP's own scrape jobs for a host
#: that happens to share an instance label. ``OPEN-14`` (target discovery from
#: inventory, M2.3) is what replaces the static target list this name belongs
#: to; the name itself is expected to survive that.
NODE_JOB: Final = "node"

#: Filesystem types excluded from the disk templates. Pseudo-filesystems have
#: a size and a utilisation, and including them reports a full ``squashfs`` or
#: a near-empty ``tmpfs`` as though it were the host's disk.
_PSEUDO_FILESYSTEMS: Final = "tmpfs|overlay|squashfs|ramfs|devtmpfs|efivarfs|iso9660"

#: Network interfaces excluded from the throughput template: loopback and
#: container-side virtual interfaces, which double-count traffic that the real
#: interface already carries.
_VIRTUAL_INTERFACES: Final = "lo|veth.*|docker[0-9].*|br-.*|cni.*|flannel.*"

#: Default ``rate()`` window. Wide enough to survive one missed scrape at the
#: 15–30 s intervals this stack uses, and :func:`render` takes it as an
#: argument so M2.2's range endpoint can widen it with the step: a step larger
#: than the window samples disjoint windows and under-reports.
DEFAULT_WINDOW_SECONDS: Final = 300

LINUX: Final = "LINUX"


class TemplateError(Exception):
    """Base class for a render that must not produce a query.

    Deliberately **not** an ``AppError``. These are decided by the caller:
    an unknown template id is a ``422`` on an endpoint that accepts one, while
    an unsupported OS family is a ``data_state`` of ``unsupported`` on a
    response that is otherwise a success. Making them HTTP errors here would
    turn the second case into a failed request, which PRD §4.4 forbids.
    """


class UnknownTemplateError(TemplateError):
    """No such template id. The catalogue is closed; there is no fallback."""


class UnsupportedOsFamilyError(TemplateError):
    """This template has no query for that OS family — ``unsupported``."""


class InvalidInstanceError(TemplateError):
    """An instance label that must not be interpolated into a query."""


@dataclass(frozen=True, slots=True)
class MetricTemplate:
    """One catalogue entry (docs/API_CONTRACTS.md §15.4).

    ``queries`` is keyed by OS family. A family that is absent is
    **unsupported**, which is the whole point of the structure: there is no
    way to express "supported but empty", so a missing exporter cannot be
    reported as a host with no load.
    """

    id: str
    unit: str
    description: str
    queries: Mapping[str, str]
    #: ``{"warning": …, "critical": …}``, or ``None`` where no threshold is
    #: universally meaningful. ``load_average`` is the clearest case: 4 is
    #: idle on 16 cores and desperate on one, so a default would be wrong
    #: more often than right. Thresholds for those arrive per server with
    #: M2.2's threshold evaluation.
    default_thresholds: Mapping[str, float] | None = None

    @property
    def applies_to_os(self) -> tuple[str, ...]:
        return tuple(sorted(self.queries))


#: The ten templates AD-04 names, in the order the contract lists them.
#: ``$selector`` is the instance matcher and ``$window`` the rate window;
#: :func:`render` substitutes both and nothing else.
CATALOGUE: Final[tuple[MetricTemplate, ...]] = (
    MetricTemplate(
        id="cpu_utilisation",
        unit="percent",
        description="Processor time in use, averaged across all cores.",
        queries={
            # Idle time is the only mode that can be subtracted from 1 to get
            # "busy" without enumerating every other mode — and the set of
            # modes differs by kernel version, so enumerating them is a bug
            # waiting for a kernel upgrade.
            LINUX: (
                "100 * (1 - avg without (cpu, mode) "
                '(rate(node_cpu_seconds_total{mode="idle",$selector}[$window])))'
            ),
        },
        default_thresholds={"warning": 80.0, "critical": 90.0},
    ),
    MetricTemplate(
        id="memory_utilisation",
        unit="percent",
        description="Memory in use, counting reclaimable cache as available.",
        queries={
            # MemAvailable, not MemFree: free memory on a healthy Linux host
            # is near zero because the page cache uses the rest, so MemFree
            # would report every busy host as critically low.
            LINUX: (
                "100 * (1 - node_memory_MemAvailable_bytes{$selector} "
                "/ node_memory_MemTotal_bytes{$selector})"
            ),
        },
        default_thresholds={"warning": 85.0, "critical": 95.0},
    ),
    MetricTemplate(
        id="memory_available_bytes",
        unit="bytes",
        description="Memory available for allocation without swapping.",
        queries={LINUX: "node_memory_MemAvailable_bytes{$selector}"},
    ),
    MetricTemplate(
        id="disk_utilisation",
        unit="percent",
        description="Highest filesystem utilisation on the host.",
        queries={
            # max, not avg: a host with one full partition and nine empty ones
            # is out of disk, and an average says it is 10 % used.
            LINUX: (
                "100 * max without (device, mountpoint, fstype) "
                "(1 - node_filesystem_avail_bytes"
                '{fstype!~"' + _PSEUDO_FILESYSTEMS + '",$selector} '
                "/ node_filesystem_size_bytes"
                '{fstype!~"' + _PSEUDO_FILESYSTEMS + '",$selector})'
            ),
        },
        default_thresholds={"warning": 85.0, "critical": 95.0},
    ),
    MetricTemplate(
        id="disk_available_bytes",
        unit="bytes",
        description="Free space on the most constrained filesystem.",
        queries={
            LINUX: (
                "min without (device, mountpoint, fstype) "
                "(node_filesystem_avail_bytes"
                '{fstype!~"' + _PSEUDO_FILESYSTEMS + '",$selector})'
            ),
        },
    ),
    MetricTemplate(
        id="disk_io",
        unit="bytes_per_second",
        description="Disk throughput, reads and writes combined.",
        queries={
            LINUX: (
                "sum without (device) (rate(node_disk_read_bytes_total{$selector}[$window]) "
                "+ rate(node_disk_written_bytes_total{$selector}[$window]))"
            ),
        },
    ),
    MetricTemplate(
        id="network_throughput",
        unit="bytes_per_second",
        description="Network throughput, receive and transmit combined.",
        queries={
            LINUX: (
                "sum without (device) (rate(node_network_receive_bytes_total"
                '{device!~"' + _VIRTUAL_INTERFACES + '",$selector}[$window]) '
                "+ rate(node_network_transmit_bytes_total"
                '{device!~"' + _VIRTUAL_INTERFACES + '",$selector}[$window]))'
            ),
        },
    ),
    MetricTemplate(
        id="load_average",
        unit="load",
        description="One-minute load average.",
        queries={LINUX: "node_load1{$selector}"},
    ),
    MetricTemplate(
        id="uptime_seconds",
        unit="seconds",
        description="Time since the host booted.",
        queries={LINUX: "time() - node_boot_time_seconds{$selector}"},
    ),
    MetricTemplate(
        id="agent_up",
        unit="boolean",
        description="Whether the host exporter was reachable at the last scrape.",
        queries={LINUX: 'up{job="' + NODE_JOB + '",$selector}'},
    ),
)

BY_ID: Final[Mapping[str, MetricTemplate]] = {template.id: template for template in CATALOGUE}

TEMPLATE_IDS: Final[tuple[str, ...]] = tuple(template.id for template in CATALOGUE)

#: PromQL string-literal escapes, applied as one pass so an escape introduced
#: by an earlier replacement is not re-escaped by a later one.
_STRING_ESCAPES: Final = str.maketrans(
    {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
)


def escape_string_literal(value: str) -> str:
    """Escape ``value`` for use inside a PromQL double-quoted string."""
    return value.translate(_STRING_ESCAPES)


def _checked(instance: str) -> str:
    """Return ``instance`` if it may be interpolated, else refuse.

    The check is an allowlist pattern, not a denylist of dangerous
    characters: `host:port` is a narrow shape, and anything outside it is a
    bug in whatever wrote the row rather than a value to be made safe.
    """
    if not METRICS_INSTANCE_PATTERN.match(instance):
        # The value is not echoed: it is about to be refused, and
        # docs/API_CONTRACTS.md §8 forbids reflecting a rejected value.
        raise InvalidInstanceError("metrics_instance must be host:port")
    return instance


def instance_selector(instances: Sequence[str]) -> str:
    """The ``instance`` matcher for one or more hosts.

    One host gets an equality matcher, which is both cheaper for Prometheus
    and exactly right. Several get an anchored alternation — Prometheus
    anchors ``=~`` fully, so each alternative is regex-escaped to keep ``.``
    in a hostname from matching any character.
    """
    checked = [_checked(instance) for instance in instances]
    if not checked:
        # Fail closed. An empty selector renders `{}`, which either fails to
        # parse or — with another matcher present — silently becomes a query
        # across every host in the estate: the cardinality abuse AD-04 exists
        # to prevent, arrived at by passing an empty list.
        raise InvalidInstanceError("at least one metrics_instance is required")
    if len(checked) == 1:
        return f'instance="{escape_string_literal(checked[0])}"'
    alternation = "|".join(escape_string_literal(re.escape(value)) for value in checked)
    return f'instance=~"{alternation}"'


def render(
    template_id: str,
    os_family: str,
    instances: Sequence[str],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> str:
    """Render one template to PromQL.

    Raises :class:`UnknownTemplateError`, :class:`UnsupportedOsFamilyError` or
    :class:`InvalidInstanceError`; it never returns a query it could not build.
    """
    template = BY_ID.get(template_id)
    if template is None:
        raise UnknownTemplateError(f"unknown metric template: {template_id!r}")
    query = template.queries.get(os_family)
    if query is None:
        raise UnsupportedOsFamilyError(
            f"template {template_id!r} has no query for OS family {os_family!r}"
        )
    if window_seconds <= 0:
        raise InvalidInstanceError("window_seconds must be positive")
    # ``substitute``, not ``safe_substitute``: an unknown placeholder in a
    # catalogue entry is a typo that must fail here rather than reach
    # Prometheus as a literal ``$selector``.
    return Template(query).substitute(
        selector=instance_selector(instances),
        window=f"{window_seconds}s",
    )


def render_scrape_timestamp(os_family: str, instances: Sequence[str]) -> str:
    """The time of each host's latest scrape, as a query on ``agent_up``.

    ``timestamp()`` over a plain instant selector returns the **sample's** own
    time, not the evaluation time — which is the only way to learn how old a
    reading is, because an instant query stamps every result it returns with
    the moment it was evaluated. ``up`` is written on every scrape attempt,
    so it dates the scrape that produced the host's ``node_*`` series.

    Not a template a client can name: it is derived from ``agent_up`` here, so
    the allowlist still holds (AD-04). A family without ``agent_up`` is
    unsupported, exactly as for any template.
    """
    return f"timestamp({render('agent_up', os_family, instances)})"


def render_agent_window(os_family: str, instances: Sequence[str], window_seconds: int) -> str:
    """Did any scrape of each host succeed in the last ``window_seconds``?

    ``max_over_time`` over ``up`` is 1 if at least one scrape succeeded in the
    window and 0 only if every one failed — which is exactly "unreachable for
    this long", with no state kept on our side. A host Prometheus has no
    series for at all is simply absent from the result.

    Derived from ``agent_up`` like :func:`render_scrape_timestamp`, so the
    allowlist still holds (AD-04).
    """
    if window_seconds <= 0:
        raise InvalidInstanceError("window_seconds must be positive")
    return f"max_over_time({render('agent_up', os_family, instances)}[{window_seconds}s])"
