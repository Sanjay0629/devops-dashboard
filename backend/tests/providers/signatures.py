"""The signature check behind SECURITY.md rule 1, as a function so it can be tested.

A test that inspects signatures is only worth having if it is shown to *fail* on a bad one
(``test_signatures.py`` does that), and the conformance harness reuses it for every adapter.
"""

from __future__ import annotations

import inspect
import uuid
from typing import get_type_hints

from app.providers.types import ExecutionHandle, ServerRef, ServiceAction, ServiceRef

#: The only types a parameter of an adapter method may have. Adding a parameter means adding
#: a type here, on purpose, where a reviewer will see it.
ALLOWED_PARAMETER_TYPES = frozenset(
    {ServerRef, ServiceRef, ServiceAction, ExecutionHandle, uuid.UUID}
)

FORBIDDEN_NAMES = frozenset(
    {
        "command", "cmd", "script", "argv", "args", "kwargs", "shell", "document",
        "document_name", "params", "parameters", "payload", "body", "line", "input",
    }
)  # fmt: skip


def violations(cls: type) -> list[str]:
    """Every way a method of ``cls`` (public or ``_submit``) could carry free-form input."""
    found: list[str] = []
    for name, member in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith("__") or (name.startswith("_") and name != "_submit"):
            continue
        hints = get_type_hints(member)
        for pname, param in inspect.signature(member).parameters.items():
            if pname == "self":
                continue
            where = f"{cls.__name__}.{name}({pname})"
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                found.append(f"{where}: variadic parameter")
            if pname.lower() in FORBIDDEN_NAMES:
                found.append(f"{where}: parameter name suggests a command")
            if hints.get(pname) not in ALLOWED_PARAMETER_TYPES:
                found.append(f"{where}: type {hints.get(pname)!r} is not a closed type")
    return found
