"""Checks on the committed AWS artifacts, as functions, so each can be shown to FAIL.

A test that only passes on the real file proves nothing if nobody has seen it fail on a bad one;
``test_document.py`` and ``test_policy.py`` weaken a copy of each artifact and assert a named
violation appears (the M4.1 signature-check precedent).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.providers.types import UNIT_NAME

AWS_DIR = Path(__file__).resolve().parents[4] / "infra" / "aws"
DOCUMENT_PATH = AWS_DIR / "ssm" / "DCP-ServiceControl.json"
POLICY_PATH = AWS_DIR / "iam-execution-policy.json"
TRUST_PATH = AWS_DIR / "trust-policy.json"

ACTIONS = ["start", "stop", "restart", "status"]
SHELL_DOCUMENT_ARNS = {
    "arn:aws:ssm:*::document/AWS-RunShellScript",
    "arn:aws:ssm:*::document/AWS-RunPowerShellScript",
}
# Constructs that would let a parameter's value become code.
_FORBIDDEN_IN_SCRIPT = (r"\{\{", r"\beval\b", r"\bsh\s+-c\b", r"\bbash\s+-c\b", "`")


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def script_of(document: dict[str, Any]) -> str:
    return "\n".join(document["mainSteps"][0]["inputs"]["runCommand"])


def document_violations(document: dict[str, Any]) -> list[str]:
    found: list[str] = []
    if document.get("schemaVersion") != "2.2":
        found.append("schemaVersion is not 2.2")
    parameters = document.get("parameters", {})
    if set(parameters) != {"Action", "UnitName"}:
        found.append(f"parameters are not exactly Action and UnitName: {sorted(parameters)}")
    for name, spec in parameters.items():
        if spec.get("interpolationType") != "ENV_VAR":
            found.append(f"{name} is not interpolated as ENV_VAR")
    if parameters.get("Action", {}).get("allowedValues") != ACTIONS:
        found.append("Action allowedValues is not exactly start, stop, restart, status")
    pattern = parameters.get("UnitName", {}).get("allowedPattern")
    if not pattern or not pattern.startswith("^") or not pattern.endswith("$"):
        found.append("UnitName has no anchored allowedPattern")
    steps = document.get("mainSteps", [])
    if len(steps) != 1 or steps[0].get("action") != "aws:runShellScript":
        found.append("there is not exactly one aws:runShellScript step")
        return found
    script = script_of(document)
    for forbidden in _FORBIDDEN_IN_SCRIPT:
        if re.search(forbidden, script):
            found.append(f"script contains a forbidden construct: {forbidden}")
    for needed in ('"$action"', '"$unit"', "invalid_action", "invalid_unit"):
        if needed not in script:
            found.append(f"script does not re-validate the parameters ({needed} missing)")
    # Every systemctl call passes the unit after `--`, quoted.
    for line in script.splitlines():
        if "systemctl" in line and "systemctl show --property=LoadState" not in line:
            for call in re.findall(r"systemctl [^;]*", line):
                if '-- "$unit"' not in call:
                    found.append(f"systemctl call without a quoted unit after --: {call.strip()}")
    return found


def unit_pattern_matches_registered_pattern(document: dict[str, Any]) -> list[str]:
    """SSM's ``allowedPattern`` must accept exactly what ``services.service_name`` accepts."""
    pattern = re.compile(document["parameters"]["UnitName"]["allowedPattern"])
    samples = {
        "nginx.service": True,
        "getty@tty1.service": True,
        "a.b-c_d.timer": True,
        "x.target": True,
        "nginx": False,
        "nginx.service ": False,
        "nginx.service\n": False,
        "a;b.service": False,
        "$(id).service": False,
        "`id`.service": False,
        "-x.service": True,  # accepted by the pattern; the script's `-*` case and `--` refuse it
        "a/b.service": False,
        "": False,
        ("a" * 129) + ".service": False,
    }
    problems = []
    for value, expected in samples.items():
        if (pattern.fullmatch(value) is not None) is not expected:
            problems.append(f"allowedPattern disagrees about {value!r}")
        registered = UNIT_NAME.fullmatch(value) is not None
        if value not in ("-x.service",) and registered is not expected:
            problems.append(f"UNIT_NAME disagrees about {value!r}")
    return problems


def _statements(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return list(policy["Statement"])


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def policy_violations(policy: dict[str, Any]) -> list[str]:
    found: list[str] = []
    allowed_actions: list[str] = []
    for statement in _statements(policy):
        actions = _as_list(statement["Action"])
        if statement["Effect"] == "Allow":
            allowed_actions += actions
        for action in actions:
            if "*" in action:
                found.append(f"wildcard action {action}")
            if action.split(":")[0].lower() in (
                "iam",
                "sts",
                "organizations",
                "kms",
                "secretsmanager",
            ):
                found.append(f"action outside SSM: {action}")
    denied: set[str] = set()
    for statement in _statements(policy):
        if statement["Effect"] == "Deny" and "ssm:SendCommand" in _as_list(statement["Action"]):
            denied |= set(_as_list(statement["Resource"]))
    if not denied >= SHELL_DOCUMENT_ARNS:
        found.append("the shell documents are not both explicitly denied")
    send = [
        s
        for s in _statements(policy)
        if s["Effect"] == "Allow" and "ssm:SendCommand" in _as_list(s["Action"])
    ]
    documents = [r for s in send for r in _as_list(s["Resource"]) if ":document/" in r]
    if len(documents) != 1 or not documents[0].endswith(":document/DCP-ServiceControl"):
        found.append(f"SendCommand is not allowed on exactly the one document: {documents}")
    instances = [s for s in send if any(":instance/" in r for r in _as_list(s["Resource"]))]
    if len(instances) != 1:
        found.append("there is not exactly one SendCommand statement for instances")
    elif (
        instances[0].get("Condition", {}).get("StringEquals", {}).get("ssm:resourceTag/DcpManaged")
        != "true"
    ):
        found.append("instances are not restricted by the DcpManaged tag")
    for statement in send:
        if statement["Resource"] == "*" or "*" in _as_list(statement["Resource"]):
            found.append("SendCommand allowed on Resource *")
    if "ssm:SendCommand" not in allowed_actions:
        found.append("SendCommand is not allowed at all")
    return found


def trust_violations(trust: dict[str, Any]) -> list[str]:
    found = []
    for statement in _statements(trust):
        if statement["Effect"] != "Allow" or statement["Action"] != "sts:AssumeRole":
            found.append("trust policy has something other than an Allow of sts:AssumeRole")
        principal = statement.get("Principal", {})
        aws = principal.get("AWS") if isinstance(principal, dict) else None
        if not isinstance(aws, str) or ":role/" not in aws or aws.endswith(":root") or "*" in aws:
            found.append(f"trust principal is not one specific role: {principal}")
    return found
