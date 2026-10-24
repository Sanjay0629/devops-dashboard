"""Scope as a SQL predicate (docs/SECURITY.md §20).

§20 requires the caller's readable set to be applied **inside** the query, so
rows they may not see are never fetched, never counted and never paged over.
That cannot be `permits()` called per row, so this module translates the same
grants into a ``WHERE`` clause.

**This is a translation of the matcher, not a second decision.** Rule 7 — "one
matcher" — is kept by construction and then checked: the predicate below is
built from the same normalised scope with the same rules in the same order,
and ``tests/integration/test_scope_filtering.py`` cross-checks the two against
a table of grants and rows, failing if any row is selected by one and refused
by the other. If a rule changes in ``matcher.py``, that test is what fails.

The translation, rule by rule:

* **Rule 3 (any deny wins)** — ``(any allow matches) AND NOT (any deny
  matches)``, which is the matcher's deny-first order written as boolean
  algebra. A deny whose scope is empty or unreadable applies to everything, so
  it collapses the whole predicate to false.
* **Rule 4 (production is never implicit)** — a grant that does not name
  ``production`` literally contributes ``environment <> 'production'``, so
  ``["*"]`` does not reach it.
* **Rule 5 (fail closed)** — a malformed or empty allow scope contributes
  nothing, exactly as it grants nothing in the matcher; no allow at all is
  ``false``, not "unfiltered".
* **A-09** — a dimension the target does not have is a denial, so a grant
  naming ``application`` matches no server: a server's dimensions are its
  environment, its account and its own id. A reference value that is not a
  UUID is dropped, which is equivalent rather than lenient — in the matcher it
  is a string that no target id can equal.

Three row kinds are translated, one rule set. ``servers`` is the original.
``operations`` (M4.6, ``OPEN-107``) reads the **stored** ``operations.environment``
and the joined server's account and id, so a re-labelled server does not move an
operation in or out of scope. ``audit_events`` (M4.6, ``OPEN-109``) has an
``environment`` (nullable) and no other dimension: a grant constraining a
``cloud_account``, ``server`` or ``application`` reaches no event (A-09), and an
event with no environment is reached only by a grant that covers every environment
(``wildcard``, or all four named -- the bootstrap administrator's shape) and names no specific
account, server or application: ``matcher.permits_environmentless``.
``applications`` are deliberately not scope-filtered at this milestone; the
reason, and the decision it needs, are in ``app/inventory/queries.py`` and in
PROGRESS.md as ``OPEN-27``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, false, not_, or_, true
from sqlalchemy.orm import QueryableAttribute

from app.audit.models import AuditEvent
from app.authz.matcher import (
    PRODUCTION,
    WILDCARD,
    Grant,
    covers_every_environment,
    normalise_scope,
)
from app.deployments.models import Deployment, DeploymentTarget
from app.inventory.models import Server
from app.operations.models import Operation


@dataclass(frozen=True, slots=True)
class ScopeColumns:
    """Where a row kind keeps each scope dimension.

    A dimension with no column is one the row does not have: a grant naming
    specific values for it reaches no row (A-09), exactly as the matcher says of
    a ``Target`` whose field is ``None``.
    """

    environment: QueryableAttribute[Any]
    #: ``True`` when the column may be ``NULL`` -- an event with no environment.
    environment_nullable: bool = False
    cloud_account: QueryableAttribute[Any] | None = None
    server: QueryableAttribute[Any] | None = None
    application: QueryableAttribute[Any] | None = None


SERVER_COLUMNS = ScopeColumns(
    environment=Server.environment, cloud_account=Server.cloud_account_id, server=Server.id
)
#: The stored environment (not the server's current label); the caller joins ``servers``.
OPERATION_COLUMNS = ScopeColumns(
    environment=Operation.environment, cloud_account=Server.cloud_account_id, server=Server.id
)
AUDIT_COLUMNS = ScopeColumns(environment=AuditEvent.environment, environment_nullable=True)
#: A deployment target (M5.1, ``OPEN-126``) has an environment and an application, and no account
#: or server: a grant naming a specific account or server reaches no target (A-09).
DEPLOYMENT_TARGET_COLUMNS = ScopeColumns(
    environment=DeploymentTarget.environment, application=DeploymentTarget.application_id
)


#: A deployment (M5.2, ``OPEN-140``) is scoped as its target is: by the **stored** environment and
#: application. It has no account or server: a grant naming a specific one reaches no deployment.
DEPLOYMENT_COLUMNS = ScopeColumns(
    environment=Deployment.environment, application=Deployment.application_id
)


def _uuids(values: Iterable[str]) -> list[uuid.UUID]:
    """Parse the UUID references in a scope dimension, dropping the rest.

    A non-UUID entry cannot equal any stored id, so dropping it selects the
    same rows the matcher would allow. Dropping *every* entry leaves an empty
    ``IN ()``, which is false — the fail-closed answer.
    """
    parsed = []
    for value in values:
        try:
            parsed.append(uuid.UUID(value))
        except ValueError:
            continue
    return parsed


def _scope_predicate(
    scope: dict[str, frozenset[str]], columns: ScopeColumns
) -> ColumnElement[bool]:
    """One grant's scope, as the condition a row must satisfy."""
    conditions: list[ColumnElement[bool]] = []

    # Rule 4 first, as in the matcher: production is reachable only by naming
    # it. An absent `environment` dimension is unconstrained (A-09) *except*
    # for this.
    environments = scope.get("environment", frozenset())
    environment_conditions: list[ColumnElement[bool]] = []
    if PRODUCTION not in environments:
        environment_conditions.append(columns.environment != PRODUCTION)
    if environments and WILDCARD not in environments:
        environment_conditions.append(columns.environment.in_(sorted(environments)))
    if columns.environment_nullable:
        # A row with no environment is a different question (``permits_environmentless``): it is
        # reached by a scope that covers every environment, not by matching a value.
        with_value = and_(columns.environment.is_not(None), *environment_conditions)
        without = and_(
            columns.environment.is_(None), true() if covers_every_environment(scope) else false()
        )
        conditions.append(or_(with_value, without))
    else:
        conditions.extend(environment_conditions)

    accounts = scope.get("cloud_account", frozenset())
    if accounts and WILDCARD not in accounts:
        if columns.cloud_account is None:
            return false()
        conditions.append(columns.cloud_account.in_(_uuids(accounts)))

    servers = scope.get("server", frozenset())
    if servers and WILDCARD not in servers:
        if columns.server is None:
            return false()
        conditions.append(columns.server.in_(_uuids(servers)))

    applications = scope.get("application", frozenset())
    if applications and WILDCARD not in applications:
        # A-09: a row with no `application` dimension is reached by no grant that constrains one
        # to specific ids. ``["*"]`` constrains nothing and is satisfied -- the M1.5 amendment,
        # see ``matcher.py``. A deployment target (M5.1) does have one.
        if columns.application is None:
            return false()
        conditions.append(columns.application.in_(_uuids(applications)))

    return and_(*conditions) if conditions else true()


def _readable(
    grants: Sequence[Grant], resource_type: str, action: str, columns: ScopeColumns
) -> ColumnElement[bool]:
    """The ``WHERE`` clause selecting the rows these grants reach."""
    relevant = [
        grant for grant in grants if grant.resource_type == resource_type and grant.action == action
    ]
    if not relevant:
        return false()

    allows: list[ColumnElement[bool]] = []
    denies: list[ColumnElement[bool]] = []
    for grant in relevant:
        scope = normalise_scope(grant.scope)
        if grant.effect == "DENY":
            # Mirrors the matcher: an unreadable or empty scope on a deny
            # applies to everything rather than to nothing.
            denies.append(true() if not scope else _scope_predicate(scope, columns))
            continue
        if grant.effect != "ALLOW":
            # The CHECK constraint forbids a third value, so one here means
            # the database is not what this code expects. Deny the lot.
            return false()
        if not scope:
            # None (malformed) or {} (empty): grants nothing, and must not
            # revoke a sound grant alongside it.
            continue
        allows.append(_scope_predicate(scope, columns))

    if not allows:
        return false()
    permitted = or_(*allows)
    if denies:
        permitted = and_(permitted, not_(or_(*denies)))
    return permitted


def readable_servers(
    grants: Sequence[Grant], resource_type: str, action: str
) -> ColumnElement[bool]:
    """The ``WHERE`` clause selecting the servers these grants reach."""
    return _readable(grants, resource_type, action, SERVER_COLUMNS)


def readable_operations(
    grants: Sequence[Grant], resource_type: str, action: str
) -> ColumnElement[bool]:
    """The operations these grants reach: stored environment, joined server's account and id.

    The statement must join ``servers`` on ``operations.target_server_id``.
    """
    return _readable(grants, resource_type, action, OPERATION_COLUMNS)


def readable_audit_events(grants: Sequence[Grant]) -> ColumnElement[bool]:
    """The audit events ``audit:read`` reaches (``OPEN-109``): by environment alone."""
    return _readable(grants, "audit", "read", AUDIT_COLUMNS)


def readable_deployment_targets(
    grants: Sequence[Grant], resource_type: str, action: str
) -> ColumnElement[bool]:
    """The deployment targets these grants reach: environment and application (``OPEN-126``)."""
    return _readable(grants, resource_type, action, DEPLOYMENT_TARGET_COLUMNS)


def readable_deployments(
    grants: Sequence[Grant], resource_type: str, action: str
) -> ColumnElement[bool]:
    """The deployments these grants reach: stored environment and application (``OPEN-140``)."""
    return _readable(grants, resource_type, action, DEPLOYMENT_COLUMNS)
