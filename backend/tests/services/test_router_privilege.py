"""The Administrator-only rule for action policies (docs/API_CONTRACTS.md §16.5).

Unit-level because no seeded role holds ``server:write`` without also holding
``admin:users`` — only ADMINISTRATOR has either — so an end-to-end test cannot
construct the caller this rule exists for. The rule is a guard for the day a
role gets ``server:write`` alone, and it is tested against that caller directly
rather than left as code nothing can reach.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.authz.dependencies import Authorized
from app.authz.matcher import Grant
from app.inventory.service import NotFoundError
from app.services.router import _require_administrator

ALL = {"environment": ["development", "staging", "production", "other"], "cloud_account": ["*"]}


def caller(*permissions: tuple[str, str]) -> Authorized:
    grants = tuple(Grant(resource, action, "ALLOW", ALL) for resource, action in permissions)
    user: Any = SimpleNamespace(id="u", email="writer@example.test")
    return Authorized(user=user, session_id="s", grants=grants)


def test_a_server_writer_who_is_not_an_administrator_is_refused() -> None:
    with pytest.raises(NotFoundError):
        _require_administrator(caller(("server", "read"), ("server", "write")))


def test_an_administrator_is_allowed() -> None:
    _require_administrator(caller(("server", "write"), ("admin", "users")))


def test_a_caller_with_no_grants_is_refused() -> None:
    with pytest.raises(NotFoundError):
        _require_administrator(caller())
