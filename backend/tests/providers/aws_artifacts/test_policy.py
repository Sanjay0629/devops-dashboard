"""The execution role's policy, parsed (``OPEN-87``).

The runtime never tries a shell document to prove the deny, so the committed policy is the thing
that is checked, here and by ``scripts/verify_aws_role.py`` against the real role.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from tests.providers.aws_artifacts import checks


@pytest.fixture
def policy() -> dict:
    return checks.load(checks.POLICY_PATH)


def test_the_committed_policy_passes_every_check(policy: dict) -> None:
    assert checks.policy_violations(policy) == []


def test_the_committed_trust_policy_names_one_role(policy: dict) -> None:
    assert checks.trust_violations(checks.load(checks.TRUST_PATH)) == []


def _without_statement(policy: dict, sid: str) -> dict:
    bad = copy.deepcopy(policy)
    bad["Statement"] = [s for s in bad["Statement"] if s["Sid"] != sid]
    return bad


def test_dropping_the_deny_is_caught(policy: dict) -> None:
    bad = _without_statement(policy, "DenyGeneralPurposeShellDocuments")
    assert any("explicitly denied" in v for v in checks.policy_violations(bad))


def test_denying_only_one_shell_document_is_caught(policy: dict) -> None:
    bad = copy.deepcopy(policy)
    deny = next(s for s in bad["Statement"] if s["Effect"] == "Deny")
    deny["Resource"] = deny["Resource"][:1]
    assert any("explicitly denied" in v for v in checks.policy_violations(bad))


def test_a_wildcard_action_is_caught(policy: dict) -> None:
    bad = copy.deepcopy(policy)
    bad["Statement"][2]["Action"] = ["ssm:*"]
    assert any("wildcard" in v for v in checks.policy_violations(bad))


def test_sendcommand_on_every_document_is_caught(policy: dict) -> None:
    bad = copy.deepcopy(policy)
    bad["Statement"][0]["Resource"] = ["*"]
    found = checks.policy_violations(bad)
    assert any("exactly the one document" in v or "Resource *" in v for v in found)


def test_losing_the_tag_condition_is_caught(policy: dict) -> None:
    bad = copy.deepcopy(policy)
    del bad["Statement"][1]["Condition"]
    assert any("DcpManaged" in v for v in checks.policy_violations(bad))


def test_an_iam_or_sts_action_is_caught(policy: dict) -> None:
    bad = copy.deepcopy(policy)
    bad["Statement"][2]["Action"].append("iam:PassRole")
    assert any("outside SSM" in v for v in checks.policy_violations(bad))


@pytest.mark.parametrize("principal", ["*", "arn:aws:iam::123456789012:root"])
def test_a_broad_trust_principal_is_caught(principal: str) -> None:
    bad = {
        "Statement": [
            {"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"AWS": principal}}
        ]
    }
    assert checks.trust_violations(bad)


# --- the operator's simulator script: its decision logic, without the SDK ------------------------


def _verify_module():
    path = Path(checks.AWS_DIR).parents[1] / "scripts" / "verify_aws_role.py"
    spec = importlib.util.spec_from_file_location("verify_aws_role", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_verifier_accepts_only_the_required_decisions() -> None:
    verify = _verify_module()
    good = {
        "AWS-RunShellScript": "explicitDeny",
        "AWS-RunPowerShellScript": "explicitDeny",
        "DCP-ServiceControl": "allowed",
    }
    assert verify.evaluate(good) == []
    assert verify.evaluate({**good, "AWS-RunShellScript": "allowed"})
    assert verify.evaluate(
        {**good, "AWS-RunPowerShellScript": "implicitDeny"}
    )  # not an EXPLICIT deny
    assert verify.evaluate({**good, "DCP-ServiceControl": "implicitDeny"})
