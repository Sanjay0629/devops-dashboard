"""The permission matcher, case by case (docs/TESTING.md §8).

Table-driven because the rules are a table: five of them in docs/SECURITY.md §4
and three shapes of malformed input, and the matcher is the one function that
decides anything, so every rule has to be pinned by a case that fails if the
rule is removed.

No database, no app, no request. :func:`app.authz.matcher.permits` is pure, and
that is deliberate — it means every one of these is a real test of the rule
rather than a test of the plumbing around it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.authz.matcher import Decision, Grant, Target, permits

SERVER_A = "11111111-1111-1111-1111-111111111111"
SERVER_B = "22222222-2222-2222-2222-222222222222"
APP_A = "33333333-3333-3333-3333-333333333333"
ACCOUNT_A = "44444444-4444-4444-4444-444444444444"


def allow(scope: Any, resource_type: str = "service", action: str = "restart") -> Grant:
    return Grant(resource_type=resource_type, action=action, effect="ALLOW", scope=scope)


def deny(scope: Any, resource_type: str = "service", action: str = "restart") -> Grant:
    return Grant(resource_type=resource_type, action=action, effect="DENY", scope=scope)


STAGING_SERVER = Target(environment="staging", server=SERVER_A, cloud_account=ACCOUNT_A)
PRODUCTION_SERVER = Target(environment="production", server=SERVER_A, cloud_account=ACCOUNT_A)


#: The question most cases ask. The admin cases override it, because a
#: targetless permission is a different question with the same rules.
SERVICE_RESTART: tuple[str, str] = ("service", "restart")
ADMIN_USERS: tuple[str, str] = ("admin", "users")


def case(
    label: str,
    grants: list[Grant],
    target: Target | None,
    allowed: bool,
    reason: str,
    permission: tuple[str, str] = SERVICE_RESTART,
) -> Any:
    return pytest.param(grants, permission, target, allowed, reason, id=label)


CASES: list[Any] = [
    case(
        "no grants at all",
        [],
        STAGING_SERVER,
        False,
        "no_matching_grant",
    ),
    case(
        "a grant for a different action",
        [allow({"server": ["*"]}, action="stop")],
        STAGING_SERVER,
        False,
        "no_matching_grant",
    ),
    case(
        "a grant for a different resource type",
        [allow({"server": ["*"]}, resource_type="deployment")],
        STAGING_SERVER,
        False,
        "no_matching_grant",
    ),
    case(
        "exact match on every dimension",
        [allow({"environment": ["staging"], "server": [SERVER_A], "cloud_account": [ACCOUNT_A]})],
        STAGING_SERVER,
        True,
        "granted",
    ),
    case(
        "wildcard on one dimension",
        [allow({"environment": ["staging"], "server": ["*"]})],
        STAGING_SERVER,
        True,
        "granted",
    ),
    case(
        "a dimension the grant does not mention is unconstrained (A-09)",
        [allow({"environment": ["staging"]})],
        STAGING_SERVER,
        True,
        "granted",
    ),
    case(
        "conjunction: one dimension matches, another does not",
        [allow({"environment": ["staging"], "server": [SERVER_B]})],
        STAGING_SERVER,
        False,
        "server_out_of_scope",
    ),
    case(
        "wrong environment",
        [allow({"environment": ["development"], "server": ["*"]})],
        STAGING_SERVER,
        False,
        "environment_out_of_scope",
    ),
    case(
        "production is not granted by a wildcard (rule 4)",
        [allow({"environment": ["*"], "server": ["*"]})],
        PRODUCTION_SERVER,
        False,
        "production_not_explicit",
    ),
    case(
        "production is not granted by omitting the dimension (rule 4)",
        [allow({"server": ["*"]})],
        PRODUCTION_SERVER,
        False,
        "production_not_explicit",
    ),
    case(
        "production named explicitly is granted",
        [allow({"environment": ["production"], "server": ["*"]})],
        PRODUCTION_SERVER,
        True,
        "granted",
    ),
    case(
        "explicit deny beats a matching allow (rule 3)",
        [allow({"environment": ["staging"], "server": ["*"]}), deny({"server": [SERVER_A]})],
        STAGING_SERVER,
        False,
        "explicit_deny",
    ),
    case(
        "a deny scoped elsewhere does not bite",
        [allow({"environment": ["staging"], "server": ["*"]}), deny({"server": [SERVER_B]})],
        STAGING_SERVER,
        True,
        "granted",
    ),
    case(
        "an empty scope grants nothing (DATABASE.md §3.5)",
        [allow({})],
        STAGING_SERVER,
        False,
        "empty_scope",
    ),
    case(
        "an empty scope grants nothing even without a target",
        [allow({}, resource_type="admin", action="users")],
        None,
        False,
        "empty_scope",
        ADMIN_USERS,
    ),
    case(
        "malformed scope: not an object",
        [allow(["staging"])],
        STAGING_SERVER,
        False,
        "malformed_scope",
    ),
    case(
        "malformed scope: unknown dimension",
        [allow({"team": ["payments"]})],
        STAGING_SERVER,
        False,
        "malformed_scope",
    ),
    case(
        "malformed scope: a bare string where a list belongs",
        [allow({"environment": "production"})],
        PRODUCTION_SERVER,
        False,
        "malformed_scope",
    ),
    case(
        "malformed scope: an empty dimension list",
        [allow({"environment": []})],
        STAGING_SERVER,
        False,
        "malformed_scope",
    ),
    case(
        "malformed scope: a non-string value",
        [allow({"server": [123]})],
        STAGING_SERVER,
        False,
        "malformed_scope",
    ),
    case(
        "one malformed grant does not revoke a sound one",
        [allow({"team": ["payments"]}), allow({"environment": ["staging"]})],
        STAGING_SERVER,
        True,
        "granted",
    ),
    case(
        "a grant constraining a dimension the target does not have",
        [allow({"application": [APP_A]})],
        Target(environment="staging", server=SERVER_A),
        False,
        "target_has_no_application",
    ),
    # --- the A-09 amendment taken in M1.5; see matcher.py's docstring -------
    case(
        "a wildcard on a dimension the target does not have constrains nothing",
        [allow({"application": ["*"], "environment": ["staging"]})],
        Target(environment="staging", server=SERVER_A),
        True,
        "granted",
    ),
    case(
        "the bootstrap administrator's full scope reaches a staging server",
        [
            allow(
                {
                    "environment": ["development", "staging", "production", "other"],
                    "cloud_account": ["*"],
                    "server": ["*"],
                    "application": ["*"],
                }
            )
        ],
        STAGING_SERVER,
        True,
        "granted",
    ),
    case(
        "the same full scope reaches a server that does not exist yet",
        [
            allow(
                {
                    "environment": ["staging"],
                    "cloud_account": ["*"],
                    "server": ["*"],
                    "application": ["*"],
                }
            )
        ],
        # A registration target: environment and account are known, the id is
        # minted, and there is no application. Before the amendment every one
        # of the three wildcards refused this.
        Target(environment="staging", cloud_account=ACCOUNT_A, server=SERVER_B),
        True,
        "granted",
    ),
    case(
        "a wildcard still does not make production implicit",
        [allow({"environment": ["*"], "server": ["*"], "application": ["*"]})],
        PRODUCTION_SERVER,
        False,
        "production_not_explicit",
    ),
    case(
        "targetless check: the capability question only",
        [allow({"environment": ["staging"]}, resource_type="admin", action="users")],
        None,
        True,
        "granted",
        ADMIN_USERS,
    ),
    case(
        "targetless check: a deny still wins",
        [
            allow({"environment": ["staging"]}, resource_type="admin", action="users"),
            deny({"environment": ["staging"]}, resource_type="admin", action="users"),
        ],
        None,
        False,
        "explicit_deny",
        ADMIN_USERS,
    ),
    case(
        "an unknown effect is not a pass",
        [Grant(resource_type="service", action="restart", effect="MAYBE", scope={"server": ["*"]})],
        STAGING_SERVER,
        False,
        "unknown_effect",
    ),
]


@pytest.mark.parametrize(
    ("grants", "permission", "target", "expected_allowed", "expected_reason"),
    CASES,
)
def test_matcher_cases(
    grants: list[Grant],
    permission: tuple[str, str],
    target: Target | None,
    expected_allowed: bool,
    expected_reason: str,
) -> None:
    resource_type, action = permission
    assert permits(grants, resource_type, action, target) == Decision(
        allowed=expected_allowed, reason=expected_reason
    )


def test_resolution_failure_is_a_denial_not_an_exception() -> None:
    """Fail closed on an evaluation error (rule 5, docs/TESTING.md §8 rule 9).

    The matcher is total: it has no input for which it raises instead of
    deciding, because a raised exception on an authorisation path depends on
    every caller remembering to catch it. Fault injection into the *resolution*
    step is covered by ``tests/integration/test_admin_roles.py``.
    """
    hostile: list[Grant] = [
        Grant(resource_type="service", action="restart", effect="ALLOW", scope=None),  # type: ignore[arg-type]
        Grant(resource_type="service", action="restart", effect="ALLOW", scope=42),  # type: ignore[arg-type]
        Grant(
            resource_type="service",
            action="restart",
            effect="ALLOW",
            scope={"environment": [None]},  # type: ignore[list-item]
        ),
    ]
    for grant in hostile:
        assert permits([grant], "service", "restart", PRODUCTION_SERVER).allowed is False
        assert permits([grant], "service", "restart", None).allowed is False


def test_the_matcher_never_reads_the_request() -> None:
    """Rule 2: scope is evaluated against stored attributes only.

    A structural assertion, which is the only kind available for a negative:
    :class:`Target` is the matcher's sole source of target attributes, it is
    frozen, and it carries exactly the four scope dimensions. A request value
    can only reach the matcher by someone constructing a Target from it, which
    is visible in review.
    """
    assert Target.__dataclass_params__.frozen is True
    fields = set(Target.__dataclass_fields__)
    assert fields == {"environment", "cloud_account", "server", "application"}
