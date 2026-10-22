"""The audit registry against the live routing table and the source (M4.6, ``OPEN-114``).

``tests/audit_registry.py`` says what every state-changing route must audit. These checks keep that
statement honest without a database: no route is missing, no entry is stale, every action it names
is one the application can actually write, and every "covered elsewhere" claim names a test that
exists and mentions the action.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

from fastapi import FastAPI

from tests.audit_registry import REGISTRY
from tests.authz.test_route_coverage import routes_of

ROOT: Final = Path(__file__).resolve().parents[2]
INTEGRATION: Final = ROOT / "tests" / "integration"
STATE_CHANGING: Final = {"POST", "PUT", "PATCH", "DELETE"}


def _live(app: FastAPI) -> set[tuple[str, str]]:
    return {(info.method, info.path) for info in routes_of(app) if info.method in STATE_CHANGING}


def test_every_state_changing_route_has_an_entry_and_every_entry_has_a_route(
    app: FastAPI,
) -> None:
    """A new state-changing route must say what it audits, or the build fails."""
    live = _live(app)
    assert len(live) >= 35, "the walk found suspiciously few routes"
    unlisted = sorted(live - set(REGISTRY))
    stale = sorted(set(REGISTRY) - live)
    assert not unlisted, f"state-changing routes with no audit entry: {unlisted}"
    assert not stale, f"registry entries for routes that no longer exist: {stale}"


def test_the_kinds_are_the_closed_set_and_each_carries_what_it_needs() -> None:
    for key, entry in REGISTRY.items():
        assert entry.kind in {"driven", "elsewhere", "read_only", "own"}, key
        if entry.kind in {"driven", "elsewhere", "own"}:
            assert entry.actions, f"{key} audits nothing?"
        if entry.kind in {"elsewhere", "own"}:
            assert entry.test and "::" in entry.test, key
        if entry.kind in {"elsewhere", "read_only", "own"}:
            assert entry.why, f"{key} must say why it is not driven here"


def test_a_read_only_post_is_only_ever_a_query() -> None:
    """The escape hatch is narrow: it exists for two bodies too big for a URL, nothing else."""
    read_only = {key for key, entry in REGISTRY.items() if entry.kind == "read_only"}
    assert read_only == {
        ("POST", "/api/v1/logs/query"),
        ("POST", "/api/v1/metrics/compare"),
    }


def test_every_action_named_is_one_the_application_can_write() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "app").rglob("*.py"))
    )
    missing = sorted(
        {
            action
            for entry in REGISTRY.values()
            for action in entry.actions
            if not re.search(rf"[\"']{action}[\"']", source)
        }
    )
    assert not missing, f"actions no code writes (a typo, or a renamed event): {missing}"


def test_every_covered_elsewhere_claim_names_a_real_test_that_mentions_the_action() -> None:
    problems: list[str] = []
    for key, entry in REGISTRY.items():
        if entry.kind not in {"elsewhere", "own"}:
            continue
        assert entry.test is not None
        filename, _, name = entry.test.partition("::")
        path = INTEGRATION / filename
        if not path.exists():
            problems.append(f"{key}: {filename} does not exist")
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if name not in functions:
            problems.append(f"{key}: {name} is not a function in {filename}")
            continue
        problems.extend(
            f"{key}: {name} never mentions {action}"
            for action in entry.actions
            if action not in functions[name]
        )
    assert not problems, problems


def test_how_much_is_driven_here_is_stated_so_it_cannot_shrink_unnoticed() -> None:
    """The count of routes the completeness test does not drive is pinned, not hoped for."""
    by_kind: dict[str, int] = {}
    for entry in REGISTRY.values():
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
    assert by_kind == {"driven": 28, "elsewhere": 8, "read_only": 2, "own": 1}, by_kind
