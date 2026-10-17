"""PromQL for systemd unit state (M2.4, ``TS-02``): rendered here, never received.

The same rule as the host templates (AD-04): **no PromQL arrives from a client**,
and the only caller-influenced values that reach a query are the two this module
re-validates — an ``instance`` (``servers.metrics_instance``) and a unit name
(``services.service_name``). Both come from rows, and rows can arrive from a
seed, a migration or a direct ``UPDATE``, so neither is trusted because the API
validated it on the way in. A value outside the pattern is **refused, not
escaped**; escaping is a second layer for what the pattern might one day allow.

These are not entries in ``metrics.templates.CATALOGUE``. That catalogue is the
published list of ten metrics a client may name (§15.4); a unit-state query is
parameterised by *registered units* and is never nameable by a client, so adding
it there would put a query in a list whose point is that every entry is
addressable by id.

**The allowlist is applied on read.** The exporter's own
``--collector.systemd.unit-include`` is host configuration that DCP cannot and
must not set. What DCP controls is what it asks for: a query names the
registered units and nothing else, so an unlisted unit that a misconfigured
exporter *does* report is never read, never stored and never shown. Matching is
by ``name``; the ``node`` job is pinned so a same-named series from another
scrape job cannot be mistaken for the host's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from app.inventory.models import SERVICE_NAME_PATTERN
from app.metrics import templates
from app.metrics.templates import TemplateError, escape_string_literal

_UNIT_NAME: Final = re.compile(SERVICE_NAME_PATTERN)

#: The collector's own success series, as ``node_exporter`` labels it.
COLLECTOR: Final = "systemd"


class InvalidUnitNameError(TemplateError):
    """A unit name that must not be interpolated into a query."""


def _checked(name: str) -> str:
    if not _UNIT_NAME.fullmatch(name):
        # Not echoed: it is about to be refused (docs/API_CONTRACTS.md §8).
        raise InvalidUnitNameError("service_name does not match the unit-name pattern")
    return name


def unit_selector(names: Sequence[str]) -> str:
    """The ``name`` matcher for one or more registered units.

    Mirrors :func:`app.metrics.templates.instance_selector` for the same
    reasons: equality for one, an anchored and regex-escaped alternation for
    several (a ``.`` in ``nginx.service`` must not match any character), and a
    refusal for none — an empty selector would otherwise select every unit the
    exporter reports, which is the cardinality abuse the allowlist exists to
    prevent.
    """
    checked = [_checked(name) for name in names]
    if not checked:
        raise InvalidUnitNameError("at least one unit name is required")
    if len(checked) == 1:
        return f'name="{escape_string_literal(checked[0])}"'
    alternation = "|".join(escape_string_literal(re.escape(name)) for name in checked)
    return f'name=~"{alternation}"'


def _scoped(metric: str, instances: Sequence[str], names: Sequence[str]) -> str:
    return (
        f'{metric}{{job="{templates.NODE_JOB}",'
        f"{templates.instance_selector(instances)},{unit_selector(names)}}}"
    )


def unit_state(instances: Sequence[str], names: Sequence[str]) -> str:
    """``node_systemd_unit_state``: five series per unit, one of them ``1``."""
    return _scoped("node_systemd_unit_state", instances, names)


def restart_total(instances: Sequence[str], names: Sequence[str]) -> str:
    """Restart count. Needs ``--collector.systemd.enable-restarts-metrics``."""
    return _scoped("node_systemd_service_restart_total", instances, names)


def start_time(instances: Sequence[str], names: Sequence[str]) -> str:
    """Activation time; ``0`` when not running. Needs ``…enable-start-time-metrics``."""
    return _scoped("node_systemd_unit_start_time_seconds", instances, names)


def collector_success(instances: Sequence[str]) -> str:
    """Whether the systemd collector succeeded on its last scrape, per host."""
    return (
        f'node_scrape_collector_success{{job="{templates.NODE_JOB}",'
        f'collector="{COLLECTOR}",{templates.instance_selector(instances)}}}'
    )
