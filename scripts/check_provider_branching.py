#!/usr/bin/env python3
"""Fail if a provider name appears in code outside ``backend/app/providers/`` (OPEN-65).

IMPLEMENTATION_PLAN M4.1: "no provider-specific branching exists above the adapter layer
(reviewed, and grep-checked for provider names outside providers/)". This is the grep, done on
the syntax tree so that comments and docstrings cannot trip it and a string constant or an
identifier can.

A file may hold a provider token only if it is in ``ALLOWED`` with the reason it was reviewed.
A hit anywhere else fails, **and so does an allowlist entry with no hit** (a stale exception is
a hole nobody remembers digging). Inside an allowed file nothing is checked: that is the
review's job, and the reasons below are what the review found.

Usage: ``python scripts/check_provider_branching.py [APP_DIR]`` (default ``backend/app``).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Final

TOKENS: Final = re.compile(
    r"\b(boto3|botocore|azure|AWS|AZURE|Azure|SSM|ssm|AWS_SSM|AZURE_RUN_COMMAND|AZURE_ARC|ON_PREM)\b"
)

#: path (relative to the app directory, forward slashes) -> why it is allowed.
ALLOWED: Final[dict[str, str]] = {
    "inventory/models.py": "the closed provider and management-method enums, mirrored from the schema",
    "inventory/schemas.py": (
        "registration-time validation of closed data: a resource-id pattern per provider and a"
        " provider -> management-method table. No code path that touches a provider is chosen"
        " here (OPEN-65); revisit in M4.3 when an adapter exists that could own ServerRef"
    ),
    "inventory/seed.py": "labelled [DEMO] data that names the management method it pretends to use",
    "inventory/router_servers.py": "the provider filter's closed Literal",
    "dashboard/router.py": "the provider filter's closed Literal",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def hits(source: str) -> list[tuple[int, str]]:
    """``(line, token)`` for every provider token in code (not comments, not docstrings)."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        texts: list[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip:
                texts.append(node.value)
        elif isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.alias):
            texts.append(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            texts.append(node.module)
        elif isinstance(node, ast.arg):
            texts.append(node.arg)
        for text in texts:
            for match in TOKENS.finditer(text):
                found.append((getattr(node, "lineno", 0), match.group(1)))
    return found


def scan(app_dir: Path) -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(app_dir.rglob("*.py")):
        relative = path.relative_to(app_dir).as_posix()
        if relative.startswith("providers/"):
            continue
        found = hits(path.read_text(encoding="utf-8"))
        if found:
            result[relative] = found
    return result


def problems(app_dir: Path, allowed: dict[str, str] = ALLOWED) -> list[str]:
    result = scan(app_dir)
    out = [
        f"{rel}:{line}: provider token {token!r} outside app/providers/ and not allowlisted"
        for rel, found in result.items()
        if rel not in allowed
        for line, token in found
    ]
    out += [
        f"{rel}: allowlisted but contains no provider token -- remove the entry"
        for rel in allowed
        if rel not in result
    ]
    return out


def main(argv: list[str]) -> int:
    app_dir = Path(argv[1]) if len(argv) > 1 else Path("backend/app")
    if not app_dir.is_dir() or not any(app_dir.rglob("*.py")):
        print(f"error: {app_dir} holds no Python files; the check would pass vacuously")
        return 2
    found = problems(app_dir)
    for line in found:
        print(line)
    if found:
        return 1
    print(f"ok: no provider token outside app/providers/ except {len(ALLOWED)} reviewed files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
