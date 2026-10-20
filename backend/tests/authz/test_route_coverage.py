"""Every route is authorised, or explicitly listed as an exception.

docs/SECURITY.md §20: "A route with no permission dependency is a build
failure." This is that build failure. It walks the real application's routing
table rather than a list someone maintains, so adding a router to
``create_app`` without permission dependencies fails here — which is the point.

The exception list is deliberately not a single bucket. A public endpoint and a
session-authenticated one are different claims, so each entry says which it is
and the test asserts the claim: a "public" route must have no session
dependency, and a "session" route must have one. An entry that lies fails.

The walk itself duck-types rather than importing FastAPI's route classes.
FastAPI 0.143 stopped flattening ``include_router`` into ``app.routes`` and
wraps each included router in a private object whose effective routes are
reached through ``effective_candidates()``. A gate that silently found zero
routes would "pass" forever, so :func:`test_the_walk_finds_the_known_routes`
pins the walk itself: if a future version changes the shape again, that test
fails rather than this file quietly checking nothing.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
from fastapi import FastAPI

from app.auth.dependencies import require_session
from app.authz.dependencies import PERMISSION_ATTRIBUTE

#: Routes that carry no permission, each with why. docs/API_CONTRACTS.md §2
#: lists the public endpoints; docs/SECURITY.md §20 names the same classes.
#: "session" means authenticated but permission-free: the endpoint answers
#: about the caller themselves, so identity *is* the authorisation.
#:
#: ``POST /api/v1/webhooks/alerts`` is the fourth public endpoint in §2 (M3.3). It has
#: no session and no permission; it authenticates with a shared secret, a source
#: address and a rate limit, and ``tests/alerts/test_webhook_unit.py`` pins those.
EXCEPTIONS: Final[dict[tuple[str, str], str]] = {
    ("GET", "/healthz"): "public",
    ("GET", "/readyz"): "public",
    ("GET", "/api/v1/auth/login"): "public",
    ("GET", "/api/v1/auth/callback"): "public",
    ("POST", "/api/v1/webhooks/alerts"): "public",
    ("POST", "/api/v1/auth/logout"): "session",
    ("GET", "/api/v1/auth/session"): "session",
}

#: FastAPI's own documentation routes, mounted only when ``docs_url`` is set —
#: which ``create_app`` does for the local environment only
#: (docs/TECH_STACK.md §7), asserted by ``test_openapi_and_docs_are_local_only``.
DOCUMENTATION_PATHS: Final[frozenset[str]] = frozenset(
    {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)


class RouteInfo(NamedTuple):
    method: str
    path: str
    #: FastAPI's resolved ``Dependant`` for this route.
    dependant: Any


def _walk(node: Any) -> Iterator[Any]:
    """Yield every object in the routing tree that resolves to one route."""
    for route in getattr(node, "routes", []) or []:
        yield from _resolve(route)


def _resolve(route: Any) -> Iterator[Any]:
    if hasattr(route, "effective_candidates"):
        # An included router (FastAPI ≥ 0.143): its candidates are effective
        # route contexts carrying the prefixed path, or further routers.
        for candidate in route.effective_candidates():
            yield from _resolve(candidate)
        return
    if hasattr(route, "dependant") and hasattr(route, "path"):
        yield route


def routes_of(app: FastAPI) -> list[RouteInfo]:
    found: list[RouteInfo] = []
    for route in _walk(app):
        if route.path in DOCUMENTATION_PATHS:
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.append(RouteInfo(method=method, path=route.path, dependant=route.dependant))
    return found


def _dependency_calls(info: RouteInfo) -> list[object]:
    """Every dependency callable reachable from this route, at any depth."""
    collected: list[object] = []
    pending: list[Any] = list(info.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        collected.append(getattr(dependant, "call", None))
        pending.extend(getattr(dependant, "dependencies", []))
    return collected


def _declared_permission(info: RouteInfo) -> tuple[str, str] | None:
    for call in _dependency_calls(info):
        permission = getattr(call, PERMISSION_ATTRIBUTE, None)
        if permission is not None:
            assert isinstance(permission, tuple)
            return permission
    return None


def _requires_session(info: RouteInfo) -> bool:
    return require_session in _dependency_calls(info)


def test_the_walk_finds_the_known_routes(app: FastAPI) -> None:
    """Guards the gate below: a broken walk must fail, not pass vacuously."""
    found = {(info.method, info.path) for info in routes_of(app)}
    assert ("GET", "/healthz") in found
    assert ("GET", "/api/v1/auth/session") in found
    assert ("POST", "/api/v1/admin/users/{user_id}/roles") in found
    # Health (2) + auth (4) + admin (10) as of M1.4.
    assert len(found) >= 16, sorted(found)


def test_every_route_is_authorised_or_listed(app: FastAPI) -> None:
    """The gate itself. Reports every offender at once, not just the first."""
    unprotected = [
        f"{info.method} {info.path}"
        for info in routes_of(app)
        if _declared_permission(info) is None and (info.method, info.path) not in EXCEPTIONS
    ]
    assert not unprotected, (
        "these routes carry no permission dependency and are not listed as "
        f"exceptions in {Path(__file__).name}: {sorted(unprotected)}"
    )


def test_listed_exceptions_are_what_they_claim(app: FastAPI) -> None:
    """An entry claiming "public" must not need a session, and vice versa."""
    wrong: list[str] = []
    for info in routes_of(app):
        claim = EXCEPTIONS.get((info.method, info.path))
        if claim is None:
            continue
        if claim == "public" and _requires_session(info):
            wrong.append(f"{info.method} {info.path} is listed public but requires a session")
        if claim == "session" and not _requires_session(info):
            wrong.append(f"{info.method} {info.path} is listed session-authenticated but is not")
    assert not wrong, wrong


def test_the_exception_list_has_no_stale_entries(app: FastAPI) -> None:
    """A removed route must not leave a permanent hole in the list."""
    live = {(info.method, info.path) for info in routes_of(app)}
    assert set(EXCEPTIONS) <= live, sorted(set(EXCEPTIONS) - live)


def test_admin_routes_declare_the_permissions_the_contract_names(app: FastAPI) -> None:
    """docs/API_CONTRACTS.md §25, endpoint by endpoint.

    Transcribed from the contract rather than from the code, so renaming a
    permission in the router surfaces here as a divergence from the document
    instead of passing quietly.
    """
    expected: dict[tuple[str, str], tuple[str, str]] = {
        ("GET", "/api/v1/admin/users"): ("admin", "users"),
        ("POST", "/api/v1/admin/users"): ("admin", "users"),
        ("GET", "/api/v1/admin/users/{user_id}"): ("admin", "users"),
        ("PATCH", "/api/v1/admin/users/{user_id}"): ("admin", "users"),
        ("POST", "/api/v1/admin/users/{user_id}/roles"): ("admin", "roles"),
        ("DELETE", "/api/v1/admin/users/{user_id}/roles/{user_role_id}"): ("admin", "roles"),
        ("POST", "/api/v1/admin/users/{user_id}/sessions/terminate"): ("admin", "users"),
        ("GET", "/api/v1/admin/roles"): ("admin", "roles"),
        ("GET", "/api/v1/admin/permissions"): ("admin", "roles"),
        ("POST", "/api/v1/admin/settings/read-only-mode"): ("admin", "manage"),
    }
    declared = {(info.method, info.path): _declared_permission(info) for info in routes_of(app)}
    for key, permission in expected.items():
        assert declared.get(key) == permission, f"{key} declares {declared.get(key)!r}"


def test_approval_routes_declare_the_permissions_the_contract_names(app: FastAPI) -> None:
    """docs/API_CONTRACTS.md §22 and §25.11 – §25.14 (M4.5), transcribed from the contract."""
    expected: dict[tuple[str, str], tuple[str, str]] = {
        ("GET", "/api/v1/approvals"): ("operation", "approve"),
        ("GET", "/api/v1/approvals/{approval_id}"): ("operation", "approve"),
        ("POST", "/api/v1/operations/{operation_id}/approve"): ("operation", "approve"),
        ("POST", "/api/v1/operations/{operation_id}/reject"): ("operation", "approve"),
        ("GET", "/api/v1/admin/approval-policies"): ("admin", "policies"),
        ("POST", "/api/v1/admin/approval-policies"): ("admin", "policies"),
        ("PATCH", "/api/v1/admin/approval-policies/{policy_id}"): ("admin", "policies"),
        ("DELETE", "/api/v1/admin/approval-policies/{policy_id}"): ("admin", "policies"),
    }
    declared = {(info.method, info.path): _declared_permission(info) for info in routes_of(app)}
    for key, permission in expected.items():
        assert declared.get(key) == permission, f"{key} declares {declared.get(key)!r}"


def test_openapi_and_docs_are_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documentation routes exist in local development and nowhere else."""
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DCP_ENVIRONMENT", "production")
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    monkeypatch.setenv("DCP_CORS_ORIGINS", "https://dcp.example.invalid")
    get_settings.cache_clear()
    production = create_app(get_settings())
    paths = {getattr(route, "path", None) for route in production.routes}
    assert not paths & DOCUMENTATION_PATHS
    get_settings.cache_clear()


#: The shapes an authorisation bypass would take: a switch named after
#: skipping authorisation, or a branch on a debug/testing flag. Whitespace is
#: tolerated around the attribute access because the lines being searched are
#: detokenised, so ``settings.debug`` arrives as ``settings . debug``.
BYPASS_PATTERN: Final = re.compile(
    r"(skip|bypass|disable)[_ ]?(auth|authz|permission)|settings\s*\.\s*(debug|testing)",
    re.IGNORECASE,
)


def _code_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Source lines with comments and string literals removed.

    Prose has to be excluded or the gate trips on the module docstring that
    promises there is no bypass. ``tokenize`` does it exactly, where a
    hand-rolled stripper would not.
    """
    lines: dict[int, list[str]] = {}
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in {tokenize.COMMENT, tokenize.STRING, tokenize.FSTRING_START}:
            continue
        lines.setdefault(token.start[0], []).append(token.string)
    for number, parts in sorted(lines.items()):
        yield number, " ".join(parts)


def test_no_authorisation_bypass_exists() -> None:
    """Grep gate: no ``if DEBUG: skip_auth`` path (docs/TESTING.md §8).

    A negative that cannot be tested by calling anything, so it is tested by
    reading the source — the executable part of it.
    """
    source_root = Path(__file__).resolve().parents[2] / "app"
    hits = [
        f"{path.relative_to(source_root)}:{number}: {line.strip()}"
        for path in sorted(source_root.rglob("*.py"))
        for number, line in _code_lines(path)
        if BYPASS_PATTERN.search(line)
    ]
    assert not hits, hits


def test_the_bypass_gate_would_catch_one(tmp_path: Path) -> None:
    """The gate's own test: a planted bypass must be detected.

    Without this, "no hits" is indistinguishable from a pattern that matches
    nothing — the same reason docs/SECURITY.md §26 asks for a planted secret
    against the secret scanner.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        '"""A docstring mentioning skip_auth must not count."""\n'
        "def dependency(settings):\n"
        "    if settings.debug:\n"
        "        return None  # skip_authz\n",
        encoding="utf-8",
    )
    hits = [line for _, line in _code_lines(planted) if BYPASS_PATTERN.search(line)]
    assert len(hits) == 1, hits
    assert "settings . debug" in hits[0]


def test_alert_routes_declare_the_permissions_the_contract_names(app: FastAPI) -> None:
    """docs/API_CONTRACTS.md §20, transcribed from the contract rather than from the code."""
    expected: dict[tuple[str, str], tuple[str, str]] = {
        ("GET", "/api/v1/alerts"): ("alert", "read"),
        ("GET", "/api/v1/alerts/{alert_id}"): ("alert", "read"),
        ("POST", "/api/v1/alerts/{alert_id}/acknowledge"): ("alert", "acknowledge"),
        ("POST", "/api/v1/alerts/{alert_id}/assign"): ("alert", "acknowledge"),
    }
    declared = {(info.method, info.path): _declared_permission(info) for info in routes_of(app)}
    for key, permission in expected.items():
        assert declared.get(key) == permission, f"{key} declares {declared.get(key)!r}"


def test_operation_routes_declare_the_permissions_the_contract_names(app: FastAPI) -> None:
    """docs/API_CONTRACTS.md §17.

    ``POST …/operations`` declares ``service:read`` as its floor because the contract's permission
    is ``service:{action}`` and the action is in the body; the handler checks the action itself
    (``test_operations_api.py`` proves a viewer is refused and audited).
    """
    expected: dict[tuple[str, str], tuple[str, str]] = {
        (
            "POST",
            "/api/v1/servers/{server_id}/services/{service_name}/operations",
        ): ("service", "read"),
        ("GET", "/api/v1/operations/{operation_id}"): ("operation", "read"),
        ("POST", "/api/v1/operations/{operation_id}/cancel"): ("operation", "read"),
    }
    declared = {(info.method, info.path): _declared_permission(info) for info in routes_of(app)}
    for key, permission in expected.items():
        assert declared.get(key) == permission, f"{key} declares {declared.get(key)!r}"
