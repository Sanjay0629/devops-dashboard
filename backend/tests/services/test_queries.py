"""The systemd PromQL renderer (docs/SECURITY.md §15; AD-04).

Two caller-influenced values reach a query — an instance and a unit name — and
both come from rows, so both are re-validated here and *refused* rather than
escaped. The strings are pinned literally, as ``test_templates`` pins the ten
host templates, so a change to any of them is a visible diff in review.
"""

from __future__ import annotations

import pytest

from app.metrics.templates import InvalidInstanceError, TemplateError
from app.services import queries
from app.services.queries import InvalidUnitNameError

HOST = "web-1.internal:9100"
OTHER = "web-2.internal:9100"
SELECT = 'job="node",instance="web-1.internal:9100"'


def test_one_unit_on_one_host_is_pinned() -> None:
    assert queries.unit_state([HOST], ["nginx.service"]) == (
        f'node_systemd_unit_state{{{SELECT},name="nginx.service"}}'
    )
    assert queries.restart_total([HOST], ["nginx.service"]) == (
        f'node_systemd_service_restart_total{{{SELECT},name="nginx.service"}}'
    )
    assert queries.start_time([HOST], ["nginx.service"]) == (
        f'node_systemd_unit_start_time_seconds{{{SELECT},name="nginx.service"}}'
    )


def test_the_collector_series_is_pinned_to_the_systemd_collector() -> None:
    assert queries.collector_success([HOST]) == (
        'node_scrape_collector_success{job="node",collector="systemd",'
        'instance="web-1.internal:9100"}'
    )


def test_several_units_become_an_anchored_regex_escaped_alternation() -> None:
    """A `.` in `nginx.service` must not match any character, and the escape's
    own backslashes must survive the PromQL string literal."""
    rendered = queries.unit_state([HOST], ["nginx.service", "php8.3-fpm.service"])
    assert rendered.endswith(r'name=~"nginx\\.service|php8\\.3\\-fpm\\.service"}')


def test_several_hosts_use_the_host_template_alternation() -> None:
    rendered = queries.unit_state([HOST, OTHER], ["nginx.service"])
    assert r'instance=~"web\\-1\\.internal:9100|web\\-2\\.internal:9100"' in rendered


def test_the_node_job_is_always_pinned() -> None:
    """A same-named series from another scrape job must not be read as the host's."""
    for rendered in (
        queries.unit_state([HOST], ["a.service"]),
        queries.restart_total([HOST], ["a.service"]),
        queries.start_time([HOST], ["a.service"]),
        queries.collector_success([HOST]),
    ):
        assert 'job="node"' in rendered


# --- refused, not escaped ----------------------------------------------------

BAD_NAMES = [
    pytest.param('a".service', id="quote"),
    pytest.param('x.service"} or vector(1) #.service', id="closes-the-selector"),
    pytest.param("a.service\n", id="trailing-newline"),
    pytest.param("a\n.service", id="embedded-newline"),
    pytest.param(" a.service", id="leading-space"),
    pytest.param("a.service ", id="trailing-space"),
    pytest.param("a\\.service", id="backslash"),
    pytest.param("nginx", id="no-suffix"),
    pytest.param("nginx.exe", id="wrong-suffix"),
    pytest.param(".service", id="no-name"),
    pytest.param("a.service,b.service", id="two-units-in-one"),
    pytest.param(".*.service", id="regex-wildcard"),
    pytest.param("a|b.service", id="alternation"),
    pytest.param("a{b}.service", id="braces"),
    pytest.param("a" * 129 + ".service", id="too-long"),
    pytest.param("", id="empty"),
    pytest.param("ünit.service", id="non-ascii"),
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_bad_unit_name_is_refused(name: str) -> None:
    with pytest.raises(InvalidUnitNameError):
        queries.unit_state([HOST], [name])


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_bad_unit_name_is_refused_beside_a_good_one(name: str) -> None:
    """One bad name poisons the whole query; it is not quietly dropped."""
    with pytest.raises(InvalidUnitNameError):
        queries.unit_state([HOST], ["nginx.service", name])


def test_the_refusal_does_not_echo_the_value() -> None:
    with pytest.raises(InvalidUnitNameError) as raised:
        queries.unit_state([HOST], ['secret"-looking.service\n'])
    assert "secret" not in str(raised.value)


def test_no_unit_names_is_refused_not_rendered_as_every_unit() -> None:
    """An empty selector would select every unit the exporter reports."""
    with pytest.raises(InvalidUnitNameError):
        queries.unit_state([HOST], [])


def test_the_refusal_is_a_template_error_not_an_http_error() -> None:
    """Like the host templates: the caller decides what a refusal means."""
    assert issubclass(InvalidUnitNameError, TemplateError)


@pytest.mark.parametrize("instance", ['h"x:9100', "h:9100 or up", "no-port", ""])
def test_a_bad_instance_is_refused_by_the_host_renderer(instance: str) -> None:
    with pytest.raises(InvalidInstanceError):
        queries.unit_state([instance], ["nginx.service"])
    with pytest.raises(InvalidInstanceError):
        queries.collector_success([instance])


@pytest.mark.parametrize(
    "name",
    ["nginx.service", "php8.3-fpm.service", "getty@tty1.service", "my_app.timer", "a.b-c.target"],
)
def test_ordinary_unit_names_are_accepted(name: str) -> None:
    assert f'name="{name}"' in queries.unit_state([HOST], [name])
