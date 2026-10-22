"""Reading a thing that carries no environment (``OPEN-109``): the audit events of a login, a role
grant, a user change.

``permits`` treats a missing environment as a dimension the target lacks, which refuses any grant
that names environments -- including the bootstrap administrator's, which names all four because
rule 4 makes production reachable only by naming it. ``permits_environmentless`` is the question
those events actually ask: does this grant cover every environment, and name no specific account,
server or application?
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.approvals.models import ENVIRONMENTS
from app.authz.matcher import (
    ALL_ENVIRONMENTS,
    Grant,
    covers_every_environment,
    normalise_scope,
    permits_environmentless,
)

FOUR = ["development", "staging", "production", "other"]


def allow(scope: Any, resource: str = "audit", action: str = "read") -> Grant:
    return Grant(resource, action, "ALLOW", scope)


def deny(scope: Any) -> Grant:
    return Grant("audit", "read", "DENY", scope)


def decide(*grants: Grant) -> bool:
    return permits_environmentless(list(grants), "audit", "read").allowed


def test_the_matchers_environment_list_is_the_applications() -> None:
    """The matcher imports nothing from the application, so a copy must not drift."""
    assert frozenset(ENVIRONMENTS) == ALL_ENVIRONMENTS


@pytest.mark.parametrize(
    ("scope", "reaches"),
    [
        # What the bootstrap CLI grants: all four named, everything else "*".
        (
            {"environment": FOUR, "cloud_account": ["*"], "server": ["*"], "application": ["*"]},
            True,
        ),
        ({"environment": ["*"]}, True),
        ({"server": ["*"]}, True),  # no environment dimension: unconstrained
        ({"environment": FOUR}, True),
        ({"environment": ["*"], "cloud_account": ["*"]}, True),
        # A grant that limits the environment does not reach an event that has none.
        ({"environment": ["production"]}, False),
        ({"environment": ["staging"]}, False),
        ({"environment": ["development", "staging", "other"]}, False),  # production missing
        ({"environment": ["staging", "production", "other"]}, False),  # development missing
        # A specific account, server or application cannot be satisfied by an event without one.
        ({"environment": ["*"], "cloud_account": ["00000000-0000-0000-0000-000000000001"]}, False),
        ({"environment": ["*"], "server": ["00000000-0000-0000-0000-000000000002"]}, False),
        ({"environment": ["*"], "application": ["00000000-0000-0000-0000-000000000003"]}, False),
        # Fail closed.
        ({}, False),
        ({"unknown_dimension": ["x"]}, False),
        ({"environment": "production"}, False),
        (None, False),
    ],
)
def test_an_allow_reaches_an_environmentless_event_only_if_it_covers_every_environment(
    scope: Any, reaches: bool
) -> None:
    assert decide(allow(scope)) is reaches


def test_no_grant_for_the_permission_is_a_denial() -> None:
    assert not decide()
    assert not decide(allow({"environment": ["*"]}, "operation", "read"))


class TestDeny:
    FULL: ClassVar[dict[str, list[str]]] = {
        "environment": FOUR,
        "cloud_account": ["*"],
        "server": ["*"],
        "application": ["*"],
    }

    def test_a_deny_that_covers_every_environment_beats_the_allow(self) -> None:
        assert not decide(allow(self.FULL), deny({"environment": ["*"]}))

    def test_a_deny_limited_to_one_environment_does_not_touch_an_environmentless_event(
        self,
    ) -> None:
        assert decide(allow(self.FULL), deny({"environment": ["production"]}))

    @pytest.mark.parametrize("scope", [{}, None, {"bogus": ["x"]}])
    def test_an_empty_or_unreadable_deny_applies_to_everything(self, scope: Any) -> None:
        assert not decide(allow(self.FULL), deny(scope))

    def test_a_malformed_allow_does_not_revoke_a_sound_one(self) -> None:
        assert decide(allow(None), allow(self.FULL))

    def test_an_unknown_effect_is_a_denial(self) -> None:
        assert not decide(Grant("audit", "read", "MAYBE", self.FULL))


@pytest.mark.parametrize(
    ("scope", "covers"),
    [
        ({}, True),
        ({"environment": ["*"]}, True),
        ({"environment": FOUR}, True),
        ({"environment": [*FOUR[:3]]}, False),
        ({"environment": ["production"]}, False),
    ],
)
def test_covers_every_environment(scope: dict[str, list[str]], covers: bool) -> None:
    normalised = normalise_scope(scope)
    assert normalised is not None
    assert covers_every_environment(normalised) is covers
