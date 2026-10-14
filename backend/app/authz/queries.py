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

Only ``servers`` is translated here. ``applications`` are deliberately not
scope-filtered at this milestone; the reason, and the decision it needs, are
in ``app/inventory/queries.py`` and in PROGRESS.md as ``OPEN-27``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import ColumnElement, and_, false, not_, or_, true

from app.authz.matcher import PRODUCTION, WILDCARD, Grant, normalise_scope
from app.inventory.models import Server


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


def _server_scope_predicate(scope: dict[str, frozenset[str]]) -> ColumnElement[bool]:
    """One grant's scope, as the condition a server row must satisfy."""
    conditions: list[ColumnElement[bool]] = []

    # Rule 4 first, as in the matcher: production is reachable only by naming
    # it. An absent `environment` dimension is unconstrained (A-09) *except*
    # for this.
    environments = scope.get("environment", frozenset())
    if PRODUCTION not in environments:
        conditions.append(Server.environment != PRODUCTION)
    if environments and WILDCARD not in environments:
        conditions.append(Server.environment.in_(sorted(environments)))

    accounts = scope.get("cloud_account", frozenset())
    if accounts and WILDCARD not in accounts:
        conditions.append(Server.cloud_account_id.in_(_uuids(accounts)))

    servers = scope.get("server", frozenset())
    if servers and WILDCARD not in servers:
        conditions.append(Server.id.in_(_uuids(servers)))

    applications = scope.get("application", frozenset())
    if applications and WILDCARD not in applications:
        # A-09: a server target has no `application` dimension, so a grant
        # constraining one to specific ids reaches no server at all. ``["*"]``
        # constrains nothing and is satisfied — the M1.5 amendment, see
        # ``matcher.py``.
        return false()

    return and_(*conditions) if conditions else true()


def readable_servers(
    grants: Sequence[Grant], resource_type: str, action: str
) -> ColumnElement[bool]:
    """The ``WHERE`` clause selecting the servers these grants reach."""
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
            denies.append(true() if not scope else _server_scope_predicate(scope))
            continue
        if grant.effect != "ALLOW":
            # The CHECK constraint forbids a third value, so one here means
            # the database is not what this code expects. Deny the lot.
            return false()
        if not scope:
            # None (malformed) or {} (empty): grants nothing, and must not
            # revoke a sound grant alongside it.
            continue
        allows.append(_server_scope_predicate(scope))

    if not allows:
        return false()
    permitted = or_(*allows)
    if denies:
        permitted = and_(permitted, not_(or_(*denies)))
    return permitted
