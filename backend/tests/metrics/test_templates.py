"""The template catalogue, its rendering, and the injection surface (M2.1).

Rendered PromQL is asserted against **expected strings**, not against a
re-render. A test that rebuilds the query from the same catalogue entry it is
checking proves only that ``str.format`` works; pinning the literal string is
what makes a silent edit to a query — a dropped ``without`` clause, a changed
filesystem filter — fail here instead of in production as a wrong number.

The injection suite is the security half (AD-04). Nothing in the product
accepts PromQL, so the only reachable vector is a ``metrics_instance`` value
that escapes its string literal; every attempt below is refused rather than
escaped, which is the fail-closed answer mandatory rule 6 asks for.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.inventory.schemas import OperatingSystem
from app.metrics import templates
from app.metrics.templates import (
    CATALOGUE,
    InvalidInstanceError,
    UnknownTemplateError,
    UnsupportedOsFamilyError,
    instance_selector,
    render,
)

INSTANCE = "node-exporter:9100"


# --- the catalogue itself ----------------------------------------------------


def test_the_catalogue_holds_exactly_the_templates_ad_04_names() -> None:
    """AD-04 lists ten templates. The list is the contract, not a suggestion."""
    assert templates.TEMPLATE_IDS == (
        "cpu_utilisation",
        "memory_utilisation",
        "memory_available_bytes",
        "disk_utilisation",
        "disk_available_bytes",
        "disk_io",
        "network_throughput",
        "load_average",
        "uptime_seconds",
        "agent_up",
    )


def test_every_os_family_is_one_the_api_can_report() -> None:
    """The catalogue's families must be values ``applies_to_os`` can carry.

    ``app/metrics/service.catalogue`` casts them into the OperatingSystem
    literal; this is the assertion that makes the cast true.
    """
    allowed = set(get_args(OperatingSystem))
    for template in CATALOGUE:
        assert set(template.queries) <= allowed, template.id


def test_windows_has_no_queries_so_it_resolves_to_unsupported() -> None:
    """OPEN-09's recorded default, asserted rather than described.

    ``windows_exporter`` cannot run in a Linux container, so a Windows query
    here would be a string that has never been executed against anything.
    Absence is what makes a WINDOWS server report ``unsupported`` instead of
    an empty chart.
    """
    for template in CATALOGUE:
        assert template.applies_to_os == ("LINUX",), template.id
        with pytest.raises(UnsupportedOsFamilyError):
            render(template.id, "WINDOWS", [INSTANCE])
        with pytest.raises(UnsupportedOsFamilyError):
            render(template.id, "OTHER", [INSTANCE])


def test_only_cost_bearing_templates_carry_default_thresholds() -> None:
    """A default threshold is a claim; absent is not zero.

    ``load_average`` is the clearest case against inventing one: 4 is idle on
    16 cores and desperate on one.
    """
    with_defaults = {
        template.id for template in CATALOGUE if template.default_thresholds is not None
    }
    assert with_defaults == {"cpu_utilisation", "memory_utilisation", "disk_utilisation"}
    for template in CATALOGUE:
        if template.default_thresholds is not None:
            assert set(template.default_thresholds) == {"warning", "critical"}
            assert template.default_thresholds["warning"] < template.default_thresholds["critical"]


def test_every_query_is_rendered_by_the_only_renderer() -> None:
    """No catalogue entry may contain a placeholder ``render`` does not fill.

    ``string.Template.substitute`` raises on an unknown placeholder, so this
    is the test that a typo'd ``$selectr`` fails at build time rather than
    reaching Prometheus as a literal dollar sign.
    """
    for template in CATALOGUE:
        for family in template.queries:
            rendered = render(template.id, family, [INSTANCE])
            assert "$" not in rendered, template.id


# --- rendered PromQL, pinned -------------------------------------------------


EXPECTED_LINUX = {
    "cpu_utilisation": (
        "100 * (1 - avg without (cpu, mode) "
        '(rate(node_cpu_seconds_total{mode="idle",instance="node-exporter:9100"}[300s])))'
    ),
    "memory_utilisation": (
        '100 * (1 - node_memory_MemAvailable_bytes{instance="node-exporter:9100"} '
        '/ node_memory_MemTotal_bytes{instance="node-exporter:9100"})'
    ),
    "memory_available_bytes": 'node_memory_MemAvailable_bytes{instance="node-exporter:9100"}',
    "disk_utilisation": (
        "100 * max without (device, mountpoint, fstype) "
        "(1 - node_filesystem_avail_bytes"
        '{fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|efivarfs|iso9660",'
        'instance="node-exporter:9100"} '
        "/ node_filesystem_size_bytes"
        '{fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|efivarfs|iso9660",'
        'instance="node-exporter:9100"})'
    ),
    "disk_available_bytes": (
        "min without (device, mountpoint, fstype) (node_filesystem_avail_bytes"
        '{fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|efivarfs|iso9660",'
        'instance="node-exporter:9100"})'
    ),
    "disk_io": (
        "sum without (device) "
        '(rate(node_disk_read_bytes_total{instance="node-exporter:9100"}[300s]) '
        '+ rate(node_disk_written_bytes_total{instance="node-exporter:9100"}[300s]))'
    ),
    "network_throughput": (
        "sum without (device) (rate(node_network_receive_bytes_total"
        '{device!~"lo|veth.*|docker[0-9].*|br-.*|cni.*|flannel.*",'
        'instance="node-exporter:9100"}[300s]) '
        "+ rate(node_network_transmit_bytes_total"
        '{device!~"lo|veth.*|docker[0-9].*|br-.*|cni.*|flannel.*",'
        'instance="node-exporter:9100"}[300s]))'
    ),
    "load_average": 'node_load1{instance="node-exporter:9100"}',
    "uptime_seconds": 'time() - node_boot_time_seconds{instance="node-exporter:9100"}',
    "agent_up": 'up{job="node",instance="node-exporter:9100"}',
}


@pytest.mark.parametrize("template_id", templates.TEMPLATE_IDS)
def test_linux_renders_the_expected_query(template_id: str) -> None:
    assert render(template_id, "LINUX", [INSTANCE]) == EXPECTED_LINUX[template_id]


def test_the_expected_table_covers_every_template() -> None:
    """Guards the table above: a new template must be pinned, not skipped."""
    assert set(EXPECTED_LINUX) == set(templates.TEMPLATE_IDS)


def test_the_rate_window_is_the_one_the_caller_asked_for() -> None:
    """M2.2's range endpoint widens the window with the step; this is why.

    A step larger than the rate window samples disjoint windows and
    under-reports, so the window has to be a parameter rather than a constant
    baked into the catalogue.
    """
    assert "[300s]" in render("cpu_utilisation", "LINUX", [INSTANCE])
    assert "[900s]" in render("cpu_utilisation", "LINUX", [INSTANCE], window_seconds=900)
    with pytest.raises(InvalidInstanceError):
        render("cpu_utilisation", "LINUX", [INSTANCE], window_seconds=0)


def test_an_unknown_template_is_refused_with_no_fallback() -> None:
    """The catalogue is closed. There is no "nearest match" and no default."""
    with pytest.raises(UnknownTemplateError):
        render("cpu_percent", "LINUX", [INSTANCE])
    with pytest.raises(UnknownTemplateError):
        render("", "LINUX", [INSTANCE])


# --- the selector ------------------------------------------------------------


def test_one_instance_gets_an_equality_matcher() -> None:
    assert instance_selector(["web1.example.com:9100"]) == 'instance="web1.example.com:9100"'


def test_several_instances_get_an_escaped_alternation() -> None:
    """``.`` must be escaped or a dot matches any character.

    Prometheus anchors ``=~`` fully, so the risk is not an unanchored match —
    it is ``web1.example.com`` matching ``web1xexampleycom``, which
    cross-attributes one host's metrics to another and is exactly the defect
    AD-04's identity join exists to prevent.
    """
    selector = instance_selector(["web1.example.com:9100", "10.0.0.4:9100"])
    assert selector == 'instance=~"web1\\\\.example\\\\.com:9100|10\\\\.0\\\\.0\\\\.4:9100"'
    # The literal that reaches Prometheus: PromQL unescapes `\\` to `\`, so
    # the regex engine sees `\.`, a literal dot.
    assert selector.count("\\\\.") == 5


def test_an_empty_instance_list_is_refused_not_rendered_wide() -> None:
    """Fail closed: an empty selector is a query across the whole estate.

    With another matcher present — ``agent_up`` has ``job="node"`` —
    ``{job="node",}`` parses and returns every host, which is the cardinality
    abuse AD-04 exists to stop, reached by passing an empty list.
    """
    with pytest.raises(InvalidInstanceError):
        instance_selector([])
    with pytest.raises(InvalidInstanceError):
        render("agent_up", "LINUX", [])


# --- injection ---------------------------------------------------------------


#: Each of these would, concatenated naively, produce a *valid* PromQL query
#: that returns something other than the requested host's metrics.
INJECTION_ATTEMPTS = [
    pytest.param('node:9100"} or up{', id="close-the-literal-and-or"),
    pytest.param('node:9100"}', id="close-the-selector"),
    pytest.param('"} or vector(1) #', id="comment-out-the-rest"),
    pytest.param("node:9100\\", id="trailing-backslash-escapes-the-quote"),
    pytest.param('node:9100\\"', id="pre-escaped-quote"),
    pytest.param("node:9100\n} or up{", id="newline"),
    pytest.param("node:9100\r\n", id="crlf"),
    pytest.param("node:9100\t", id="tab"),
    # `$`-anchored in Python also matches before a trailing newline, so this
    # one was accepted until METRICS_INSTANCE_PATTERN moved to the \Z anchor.
    pytest.param("node-exporter:9100\n", id="trailing-newline"),
    pytest.param(".*:9100", id="regex-wildcard"),
    pytest.param("node:9100|other:9100", id="alternation-in-one-value"),
    pytest.param("node:9100,job=~'.*'", id="extra-matcher"),
    pytest.param("node:9100} offset 1h", id="modifier"),
    pytest.param("", id="empty-string"),
    pytest.param("   ", id="whitespace-only"),
    pytest.param("node-exporter", id="no-port"),
    pytest.param("node-exporter:9100:9100", id="two-ports"),
    pytest.param("node-exporter:999999", id="port-too-long"),
    pytest.param("${selector}", id="placeholder-injection"),
    pytest.param("$window", id="placeholder-window"),
]


@pytest.mark.parametrize("hostile", INJECTION_ATTEMPTS)
def test_a_hostile_instance_label_is_refused(hostile: str) -> None:
    """Refused, not escaped. The shape is `host:port` and nothing else is one."""
    with pytest.raises(InvalidInstanceError):
        instance_selector([hostile])
    with pytest.raises(InvalidInstanceError):
        render("cpu_utilisation", "LINUX", [hostile])


@pytest.mark.parametrize("hostile", INJECTION_ATTEMPTS)
def test_a_hostile_label_is_refused_even_beside_a_valid_one(hostile: str) -> None:
    """One bad value refuses the whole query.

    Dropping it and querying the rest would answer a comparison request with
    fewer servers than asked for while looking complete — the same failure
    §15.3's ``excluded_count`` exists to make visible.
    """
    with pytest.raises(InvalidInstanceError):
        instance_selector([INSTANCE, hostile])


def test_the_escaper_handles_what_the_pattern_would_not() -> None:
    """The second layer, tested on its own.

    ``escape_string_literal`` is never reached with these values today — the
    pattern refuses them first — and that is exactly why it is tested
    directly: it is the layer that holds if the pattern is ever loosened.
    """
    assert templates.escape_string_literal('a"b') == 'a\\"b'
    assert templates.escape_string_literal("a\\b") == "a\\\\b"
    assert templates.escape_string_literal("a\nb") == "a\\nb"
    assert templates.escape_string_literal("a\r\tb") == "a\\r\\tb"
    # One pass, so a backslash introduced by escaping a quote is not escaped
    # again into `\\"`, which would close the literal one character later.
    assert templates.escape_string_literal('\\"') == '\\\\\\"'


def test_valid_instance_shapes_are_accepted() -> None:
    """The allowlist must not be so narrow that real hosts are refused."""
    for valid in (
        "node-exporter:9100",
        "web1.example.com:9100",
        "10.0.0.4:9100",
        "[2001:db8::1]:9100",
        "host-1.sub.example.org:1",
    ):
        assert instance_selector([valid]) == f'instance="{valid}"'
