"""Prometheus target discovery: inventory → ``file_sd`` file (M2.3, ``OPEN-14``).

DCP writes the list of hosts Prometheus scrapes, from the ``servers`` table, so
that registering a server results in a scraped target without anyone editing
``prometheus.yml``. Prometheus watches the file itself (``file_sd``), so **no
reload call exists anywhere**: the lifecycle API that would offer one also
offers ``/-/quit``, unauthenticated, and is not enabled.

Three rules this module exists to enforce:

**What may be a target is a policy, not a regex.** Prometheus will connect to
whatever host and port the file names, from inside the monitoring network. A
``metrics_instance`` is administrator-written, but "an administrator can make
the monitoring server connect to an arbitrary port" is a port scanner, and the
cloud metadata address is link-local. :func:`target_problem` is applied twice —
at the write boundary (so an administrator gets an error) and again here when
the file is built (so a seeded row or a direct ``UPDATE`` cannot widen it). It
cannot defend against a DNS name that *resolves* somewhere it should not;
that is the monitoring network's egress policy to enforce (docs/SECURITY.md
§7), and the limitation is recorded rather than implied away.

**One predicate selects targets, for both the file and the probe.** If the
probe could look at a server the file never lists, its ``agent_status`` would
describe a host nothing scrapes.

**A failure to build the file leaves the old file alone.** An empty file would
drop every target at once and turn a database blip into "every host is
unavailable". The writer is atomic (temp file in the same directory, then
rename) and skips the write when the content is unchanged, so Prometheus's file
watcher is not woken for nothing.

The file contains inventory ids, an environment name and ``host:port`` — no
credential and no free-text field. ``display_name`` and ``owner_team`` are
deliberately not labels: they are operator-typed text, and a label value is a
string that ends up in every series.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import Connection, text

from app.core.logging import get_logger
from app.inventory.schemas import METRICS_INSTANCE_PATTERN

logger = get_logger(__name__)

#: ``.invalid`` is reserved by RFC 6761 and can never resolve. The seeded
#: ``[DEMO]`` servers use it on purpose, so a target there is a guaranteed
#: failing scrape — and an ``UNREACHABLE`` the product would then assert about
#: a host that never existed.
RESERVED_SUFFIXES: Final = (".invalid",)

#: Only Linux hosts are scraped by the ``node`` job. A Windows host runs a
#: different exporter on a different port and has no templates (``OPEN-09``).
TARGET_OS_FAMILY: Final = "LINUX"

#: Arbitrary 64-bit key for ``pg_advisory_xact_lock``: serialises regeneration
#: so an older snapshot cannot overwrite a newer one.
_LOCK_KEY: Final = 7_201_300_023


@dataclass(frozen=True, slots=True)
class Target:
    server_id: str
    environment: str
    instance: str


def _split(instance: str) -> tuple[str, int]:
    host, _, port = instance.rpartition(":")
    return host.strip("[]"), int(port)


def target_problem(instance: str, ports: Sequence[int]) -> str | None:
    """Why ``instance`` may not be a scrape target, or ``None`` if it may.

    Returns a short machine reason, never the value: a rejected value is not
    echoed back (docs/API_CONTRACTS.md §8).
    """
    if METRICS_INSTANCE_PATTERN.match(instance) is None:
        return "not_host_port"
    host, port = _split(instance)
    if port not in ports:
        return "port_not_allowed"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.lower().endswith(RESERVED_SUFFIXES):
            return "reserved_domain"
        return None
    # An IPv4 address written as an IPv6-mapped one is still that IPv4 address:
    # `::ffff:a9fe:a9fe` is the metadata endpoint, and must not slip past a
    # test applied to the IPv6 form.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    # `is_global` is deliberately NOT the test: an exporter on a private
    # network is the normal case. These are the addresses that are never an
    # exporter: this host, the cloud metadata range, and non-unicast.
    if address.is_loopback or address.is_link_local or address.is_unspecified:
        return "address_not_allowed"
    if address.is_multicast or address.is_reserved:
        return "address_not_allowed"
    return None


def select_targets(connection: Connection, ports: Sequence[int]) -> list[Target]:
    """The servers Prometheus should scrape. The one predicate (see module doc).

    ``PENDING`` servers are included: registration grants no operational
    permission (PRD §4.3) but monitoring is read-only, and the acceptance
    criterion is that registering results in a scraped target.
    """
    rows = connection.execute(
        text(
            "SELECT id, environment, metrics_instance FROM servers"
            " WHERE deleted_at IS NULL AND status <> 'RETIRED'"
            " AND monitoring_enabled AND metrics_instance IS NOT NULL"
            " AND operating_system = :family"
            " ORDER BY metrics_instance"
        ),
        {"family": TARGET_OS_FAMILY},
    ).all()
    targets: list[Target] = []
    for row in rows:
        server_id, environment, instance = str(row[0]), str(row[1]), str(row[2])
        problem = target_problem(instance, ports)
        if problem is not None:
            # Debug, not info: the probe selects targets every few seconds, and
            # a seeded fleet of excluded demo servers would otherwise write the
            # same lines each time.
            logger.debug("target_excluded", server_id=server_id, reason=problem)
            continue
        targets.append(Target(server_id, environment, instance))
    return targets


def render_target_file(targets: Sequence[Target]) -> str:
    """Deterministic ``file_sd`` JSON: same inventory, same bytes."""
    groups = [
        {
            "targets": [target.instance],
            "labels": {"environment": target.environment, "server_id": target.server_id},
        }
        for target in targets
    ]
    return json.dumps(groups, indent=2, sort_keys=True) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    """Atomically replace ``path`` with ``content``. ``False`` if it was already that."""
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same directory, so the rename cannot cross a filesystem and is atomic: a
    # reader sees the whole old file or the whole new one, never half.
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp creates 0600; Prometheus runs as a different user.
        Path(temporary).chmod(0o644)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


@dataclass(frozen=True, slots=True)
class Regeneration:
    written: bool
    targets: int


def regenerate(connection: Connection, path: Path, ports: Sequence[int]) -> Regeneration:
    """Rebuild the target file from inventory, under a database lock.

    The caller owns the transaction. The advisory lock is transaction-scoped, so
    it is held while the rows are read **and** the file is written, and the next
    caller reads the rows only after this one has written. Without it, two
    regenerations could read at different moments and write in the other order,
    leaving the older snapshot on disk until the next periodic run.
    """
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    targets = select_targets(connection, ports)
    written = write_if_changed(path, render_target_file(targets))
    return Regeneration(written=written, targets=len(targets))


async def request_regeneration() -> None:
    """Ask the worker to rebuild the target file. Best effort, and says so.

    Called after an inventory write has **committed**. A broker that cannot be
    reached must not fail a registration that already succeeded, and the
    periodic regeneration heals the gap, so a failure here is logged and
    swallowed. The task takes no arguments (CLAUDE.md §5).

    The publish is synchronous, so it runs in a thread rather than blocking
    the event loop; ``retry=False`` because kombu's default retries a dead
    broker for several seconds inside the request that triggered it.
    """

    def publish() -> None:
        # Imported here: the worker module builds a Celery app (and configures
        # logging) at import time, which the API process should not pay for
        # unless it actually enqueues.
        from app.workers.celery_app import celery_app

        celery_app.send_task("dcp.monitoring.regenerate_targets", queue="integrations", retry=False)

    try:
        await asyncio.to_thread(publish)
    except Exception as error:
        logger.warning("target_regeneration_enqueue_failed", error=type(error).__name__)
