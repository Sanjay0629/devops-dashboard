"""The "no provider branching above the adapter layer" gate (OPEN-65), and proof it can fail."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_provider_branching.py"
APP = Path(__file__).parents[1] / "app"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_provider_branching", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load()


def test_the_real_tree_is_clean() -> None:
    assert gate.problems(APP) == []


def test_the_allowlist_is_exactly_the_reviewed_set() -> None:
    # Growing this list is a decision to be reviewed, not a way to make the check pass.
    assert set(gate.ALLOWED) == {
        "inventory/models.py",
        "inventory/schemas.py",
        "inventory/seed.py",
        "inventory/router_servers.py",
        "dashboard/router.py",
    }
    assert all(len(reason) > 20 for reason in gate.ALLOWED.values())


@pytest.mark.parametrize(
    "source",
    [
        'if account.provider == "AWS":\n    pass\n',
        "import boto3\n",
        "from botocore.exceptions import ClientError\n",
        "import azure.identity\n",
        'x = {"method": "AWS_SSM"}\n',
        "def f(ssm): ...\n",
        "AZURE_ARC = 1\n",
        'call(f"run {ON_PREM}")\n',
        "client.AWS\n",
    ],
)
def test_a_provider_token_in_code_is_found(source: str) -> None:
    assert gate.hits(source)


@pytest.mark.parametrize(
    "source",
    [
        "# AWS is mentioned here\n",
        '"""AWS Systems Manager."""\n',
        'def f():\n    """Calls AWS."""\n',
        'class C:\n    """AZURE"""\n',
        'x = "classmethod and message"\n',
        "ssmx = 1\n",
    ],
)
def test_comments_docstrings_and_lookalikes_are_not(source: str) -> None:
    assert gate.hits(source) == []


def test_a_planted_violation_fails_and_the_providers_package_is_exempt(tmp_path: Path) -> None:
    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "aws.py").write_text("import boto3\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("ok = 1\n", encoding="utf-8")
    assert gate.problems(tmp_path, allowed={}) == []
    (tmp_path / "sneaky.py").write_text('if p == "AWS":\n    pass\n', encoding="utf-8")
    found = gate.problems(tmp_path, allowed={})
    assert len(found) == 1 and found[0].startswith("sneaky.py:1")


def test_a_stale_allowlist_entry_fails(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    found = gate.problems(tmp_path, allowed={"clean.py": "no longer needs this exception"})
    assert found and "remove the entry" in found[0]


def test_an_empty_tree_is_an_error_not_a_pass(tmp_path: Path) -> None:
    assert gate.main(["x", str(tmp_path)]) == 2
