"""The one authorisation decision in this codebase (docs/SECURITY.md §4 rule 7).

Exactly one function decides whether a caller may act: :func:`permits`. It is
pure — grants in, verdict out, no database, no request, no clock beyond the
expiry already applied when the grants were read — so the whole rule set is
exercised by a table of cases rather than by standing up an endpoint
(docs/TESTING.md §8).

The rules it implements, from docs/SECURITY.md §4:

1. **Deny by default.** No matching grant is a denial, and every unexpected
   shape is a denial.
2. **The target is resolved first.** :class:`Target` carries the *stored*
   attributes of the row being acted on. Nothing a client sent reaches here.
3. **Any deny wins.** An ``effect='DENY'`` grant beats every allow. The same rule
   in docs/SECURITY.md §4 also says "most-specific wins for allow", which this
   does not compute: every allow grants the identical thing, so the most
   specific matching allow and the first matching allow have the same outcome.
   Specificity would start to matter only if allows carried differing payloads
   — and at that point this comment is the thing to revisit.
4. **Production is never implicit.** A production target requires a grant whose
   ``environment`` list contains ``"production"`` literally. ``["*"]`` does
   not, and neither does omitting the dimension.
5. **Fail closed.** A malformed scope, a dimension the target cannot satisfy,
   or anything this module does not understand denies.

Two points the contracts leave open, decided here and recorded as assumption
``A-09`` in PROGRESS.md (also written into docs/SECURITY.md §4):

* **A dimension the grant does not mention is unconstrained** — except
  ``environment`` for a production target, per rule 4. Without this,
  ``{"environment": ["staging"]}`` would grant nothing, because the example in
  docs/DATABASE.md §3.5 omits ``cloud_account`` entirely.
* **An empty scope grants nothing at all**, including permissions that have no
  target, which is docs/DATABASE.md §3.5 read literally. So a grant must say
  *something* about reach before it carries any authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

#: The only scope dimensions. A scope mentioning anything else is malformed,
#: and malformed means deny — not "ignore the part we don't recognise".
SCOPE_DIMENSIONS: Final[tuple[str, ...]] = (
    "environment",
    "cloud_account",
    "server",
    "application",
)

WILDCARD: Final = "*"
PRODUCTION: Final = "production"


@dataclass(frozen=True, slots=True)
class Grant:
    """One ``(resource_type, action)`` a user can reach, with the scope of the
    role assignment that reaches it.

    Built by ``app/authz/service.resolve_grants`` from PostgreSQL. Expired
    assignments are filtered out there, in the query, so this module never
    needs a clock.
    """

    resource_type: str
    action: str
    #: ``ALLOW`` or ``DENY`` (``role_permissions.effect``).
    effect: str
    #: ``user_roles.scope_definition`` exactly as stored.
    scope: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Target:
    """The stored attributes of the thing being acted on.

    Every field is the value read from the database, never a value from the
    request (docs/SECURITY.md §4 rule 2). ``None`` means "this target has no
    such dimension" — an application has no ``server`` — and a grant that
    constrains a dimension the target cannot satisfy is denied rather than
    waved through.
    """

    environment: str | None = None
    cloud_account: str | None = None
    server: str | None = None
    application: str | None = None

    def dimension(self, name: str) -> str | None:
        return getattr(self, name, None)


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    #: Short, stable, loggable. Goes into the ``AUTHZ_DENIED`` audit row, so it
    #: names the rule that fired and never echoes request content.
    reason: str


_ALLOWED: Final = Decision(allowed=True, reason="granted")


def permits(
    grants: Sequence[Grant],
    resource_type: str,
    action: str,
    target: Target | None = None,
) -> Decision:
    """Decide whether these grants permit ``(resource_type, action)``.

    ``target=None`` asks the capability question only — "may this caller
    administer users at all" — which is what a collection or administrative
    endpoint needs before it has a row in hand. A target makes the question
    scoped, and docs/SECURITY.md §20 requires that form to be asked *after* the
    row is loaded, so it is a separate call with the stored attributes rather
    than something a dependency could guess.
    """
    relevant = [
        grant for grant in grants if grant.resource_type == resource_type and grant.action == action
    ]
    if not relevant:
        return Decision(allowed=False, reason="no_matching_grant")

    # Rule 3, first: any deny wins. Checked before the allows so that a
    # malformed allow cannot shortcut past a deny, and evaluated against the
    # target so a deny scoped to production does not block staging. Without a
    # target there is nothing to narrow it by, so it applies.
    for grant in relevant:
        if grant.effect == "DENY":
            scope = _normalise(grant.scope)
            # Unreadable or empty scope on a *deny* means it applies: for an
            # allow those shapes grant nothing, and the safe reading of both is
            # the same one — the row restricts rather than widens.
            if not scope or target is None or _in_scope(scope, target).allowed:
                return Decision(allowed=False, reason="explicit_deny")

    reason = "no_matching_grant"
    for grant in relevant:
        if grant.effect != "ALLOW":
            # Not ALLOW and not DENY: the CHECK constraint forbids it, so
            # seeing one means the database is not what this code expects.
            return Decision(allowed=False, reason="unknown_effect")
        scope = _normalise(grant.scope)
        if scope is None:
            # Fail closed on this grant, but keep looking: one malformed grant
            # must not revoke a sound one. The reason is kept so a denial
            # reports the real cause.
            reason = "malformed_scope"
            continue
        if not scope:
            reason = "empty_scope"
            continue
        if target is None:
            return _ALLOWED
        verdict = _in_scope(scope, target)
        if verdict.allowed:
            return _ALLOWED
        reason = verdict.reason
    return Decision(allowed=False, reason=reason)


def _normalise(scope: Mapping[str, Any] | Any) -> dict[str, frozenset[str]] | None:
    """Validate a stored scope into dimension → allowed values.

    Returns ``None`` for anything that is not a well-formed scope. The column
    is ``jsonb`` and the write path validates it, but a row can predate that
    validation or have been written by hand, and "unreadable scope ⇒ deny" is
    rule 5.
    """
    if not isinstance(scope, Mapping):
        return None
    normalised: dict[str, frozenset[str]] = {}
    for key, values in scope.items():
        if key not in SCOPE_DIMENSIONS:
            return None
        # A bare string where a list belongs is the most likely hand-edit, and
        # accepting it would make {"environment": "production"} match the
        # single characters of the word.
        if not isinstance(values, list) or not values:
            return None
        if not all(isinstance(value, str) and value for value in values):
            return None
        normalised[key] = frozenset(values)
    return normalised


def _in_scope(scope: dict[str, frozenset[str]], target: Target) -> Decision:
    """Conjunction over the dimensions the grant mentions."""
    # Rule 4, before anything else: production is never implicit. A grant
    # reaches a production target only by naming production.
    if target.environment == PRODUCTION and PRODUCTION not in scope.get("environment", frozenset()):
        return Decision(allowed=False, reason="production_not_explicit")

    for dimension, allowed in scope.items():
        value = target.dimension(dimension)
        if value is None:
            # The grant constrains something this target does not have, so the
            # constraint cannot be satisfied.
            return Decision(allowed=False, reason=f"target_has_no_{dimension}")
        if WILDCARD in allowed or value in allowed:
            continue
        return Decision(allowed=False, reason=f"{dimension}_out_of_scope")
    return _ALLOWED
