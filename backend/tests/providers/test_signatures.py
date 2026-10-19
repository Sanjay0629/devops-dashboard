"""SECURITY.md rule 1: the interface has nowhere for a command to go."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.providers.base import ProviderAdapter
from app.providers.types import ServerRef, ServiceAction
from tests.providers.signatures import violations


def test_the_interface_has_no_free_form_parameter() -> None:
    assert violations(ProviderAdapter) == []


def test_the_check_covers_the_methods_that_matter() -> None:
    # Guards the guard: if inspect stopped seeing these, the test above would pass vacuously.
    import inspect

    names = {n for n, _ in inspect.getmembers(ProviderAdapter, inspect.isfunction)}
    assert {"execute_operation", "_submit", "get_operation_status", "check_connectivity"} <= names


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda self, ref: None, id="untyped"),
    ],
)
def test_an_untyped_parameter_is_a_violation(bad: Any) -> None:
    cls = type("Bad", (), {"get_server_info": bad})
    assert violations(cls)


def test_a_str_parameter_is_a_violation() -> None:
    class Bad:
        def execute_operation(self, ref: ServerRef, action: str) -> None: ...

    assert any("not a closed type" in v for v in violations(Bad))


def test_a_command_named_parameter_is_a_violation_even_with_a_closed_type() -> None:
    class Bad:
        def execute_operation(self, ref: ServerRef, command: ServiceAction) -> None: ...

    assert any("suggests a command" in v for v in violations(Bad))


def test_a_variadic_parameter_is_a_violation() -> None:
    class Bad:
        def _submit(self, ref: ServerRef, *rest: Any) -> None: ...

    assert any("variadic" in v for v in violations(Bad))


def test_a_dict_parameter_is_a_violation() -> None:
    class Bad:
        def check_connectivity(self, ref: ServerRef, options: dict[str, str]) -> None: ...

    assert violations(Bad)


def test_a_clean_class_passes() -> None:
    class Good:
        def execute_operation(
            self, ref: ServerRef, action: ServiceAction, op: uuid.UUID
        ) -> None: ...

    assert violations(Good) == []
