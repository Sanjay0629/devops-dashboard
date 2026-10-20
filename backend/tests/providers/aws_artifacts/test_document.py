"""The SSM document is the one place a shell line exists (``OPEN-81``).

Two kinds of test: a parser over the JSON, each rule shown to fail on a weakened copy, and the
script itself RUN under ``sh`` with a stand-in ``systemctl``, so hostile parameter values are
shown to be refused before anything is called.
"""

from __future__ import annotations

import copy
import shutil
import subprocess

import pytest

from tests.providers.aws_artifacts import checks


@pytest.fixture
def document() -> dict:
    return checks.load(checks.DOCUMENT_PATH)


def test_the_committed_document_passes_every_check(document: dict) -> None:
    assert checks.document_violations(document) == []
    assert checks.unit_pattern_matches_registered_pattern(document) == []


def _weaken(document: dict, change) -> dict:
    bad = copy.deepcopy(document)
    change(bad)
    return bad


@pytest.mark.parametrize(
    ("name", "change", "expected"),
    [
        (
            "plain interpolation",
            lambda d: d["parameters"]["UnitName"].pop("interpolationType"),
            "UnitName is not interpolated as ENV_VAR",
        ),
        (
            "an extra parameter",
            lambda d: d["parameters"].update({"Extra": {"type": "String"}}),
            "parameters are not exactly",
        ),
        (
            "an open action set",
            lambda d: d["parameters"]["Action"].update({"allowedValues": ["start", "bash"]}),
            "Action allowedValues",
        ),
        (
            "no pattern",
            lambda d: d["parameters"]["UnitName"].pop("allowedPattern"),
            "no anchored allowedPattern",
        ),
        (
            "braces in the script",
            lambda d: d["mainSteps"][0]["inputs"]["runCommand"].append("echo {{ UnitName }}"),
            "forbidden construct",
        ),
        (
            "eval",
            lambda d: d["mainSteps"][0]["inputs"]["runCommand"].append('eval "$unit"'),
            "forbidden construct",
        ),
        (
            "a second step",
            lambda d: d["mainSteps"].append(d["mainSteps"][0]),
            "exactly one aws:runShellScript step",
        ),
        (
            "a systemctl call without --",
            lambda d: d["mainSteps"][0]["inputs"]["runCommand"].append('systemctl kill "$unit"'),
            "systemctl call without a quoted unit",
        ),
        (
            "no validation in the script",
            lambda d: d["mainSteps"][0]["inputs"].update(
                {"runCommand": ['systemctl restart -- "$unit"']}
            ),
            "does not re-validate",
        ),
    ],
)
def test_each_rule_fails_on_a_weakened_copy(
    document: dict, name: str, change, expected: str
) -> None:
    found = checks.document_violations(_weaken(document, change))
    assert any(expected in line for line in found), (name, found)


def test_the_pattern_check_fails_on_a_loose_pattern(document: dict) -> None:
    bad = _weaken(
        document, lambda d: d["parameters"]["UnitName"].update({"allowedPattern": "^.*$"})
    )
    assert checks.unit_pattern_matches_registered_pattern(bad)


# --- run the script ------------------------------------------------------------------------------

SH = shutil.which("sh")
pytestmark_sh = pytest.mark.skipif(SH is None, reason="needs a POSIX sh")

# A stand-in for systemctl, defined as a shell function ahead of the script. It records each
# call; LoadState answers from FAKE_LOAD and ActiveState from FAKE_ACTIVE.
_PRELUDE = r"""
systemctl() {
  echo "CALL: $*" >&2
  case "$*" in
    "show --property=LoadState --value -- "*) echo "${FAKE_LOAD:-loaded}" ;;
    "show --property=ActiveState --value -- "*) echo "${FAKE_ACTIVE:-active}" ;;
  esac
}
"""


def _run(document: dict, action: str | None, unit: str | None, **env: str):
    script = _PRELUDE + checks.script_of(document)
    full_env = {"PATH": "/usr/bin:/bin"}
    if action is not None:
        full_env["SSM_Action"] = action
    if unit is not None:
        full_env["SSM_UnitName"] = unit
    full_env.update(env)
    assert SH is not None
    return subprocess.run(  # noqa: S603 - the script under test, with a stand-in systemctl
        [SH, "-c", script], env=full_env, capture_output=True, text=True, timeout=20, check=False
    )


def _calls(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.startswith("CALL:")]


@pytestmark_sh
@pytest.mark.parametrize(
    ("action", "argv"),
    [
        ("start", "start -- nginx.service"),
        ("stop", "stop -- nginx.service"),
        ("restart", "restart -- nginx.service"),
    ],
)
def test_a_valid_request_runs_exactly_one_fixed_command(
    document: dict, action: str, argv: str
) -> None:
    result = _run(document, action, "nginx.service")
    assert result.returncode == 0, result.stderr
    assert _calls(result) == [
        "CALL: show --property=LoadState --value -- nginx.service",
        f"CALL: {argv}",
    ]


@pytestmark_sh
@pytest.mark.parametrize(("active", "expected"), [("active", "active"), ("failed", "failed")])
def test_status_prints_exactly_the_active_state(document: dict, active: str, expected: str) -> None:
    result = _run(document, "status", "nginx.service", FAKE_ACTIVE=active)
    assert result.returncode == 0
    assert result.stdout.strip() == expected


@pytestmark_sh
def test_a_unit_the_host_does_not_have_is_an_error_not_inactive(document: dict) -> None:
    result = _run(document, "status", "ghost.service", FAKE_LOAD="not-found")
    assert result.returncode == 4
    assert "unit_not_found" in result.stderr
    assert result.stdout.strip() == ""  # never a state that could read as "stopped"
    assert not any("ActiveState" in c for c in _calls(result))


@pytestmark_sh
@pytest.mark.parametrize(
    "unit",
    [
        "nginx.service; id",
        "$(id).service",
        "`id`.service",
        "a b.service",
        "nginx.service\nid",
        "-rf.service",
        "--version.service",
        "nginx",
        "nginx.conf",
        "../etc/passwd.service",
        "a|b.service",
        "",
        ("a" * 200) + ".service",
    ],
)
def test_a_hostile_unit_name_is_refused_before_systemctl_is_called(
    document: dict, unit: str
) -> None:
    result = _run(document, "restart", unit)
    assert result.returncode == 64, (unit, result.stderr)
    assert _calls(result) == []


@pytestmark_sh
@pytest.mark.parametrize("action", ["kill", "restart; id", "RESTART", "status ", "", "--now"])
def test_an_unknown_action_is_refused_before_systemctl_is_called(
    document: dict, action: str
) -> None:
    result = _run(document, action, "nginx.service")
    assert result.returncode == 64
    assert _calls(result) == []


@pytestmark_sh
def test_missing_parameters_fail_closed_on_an_agent_without_env_var_support(document: dict) -> None:
    # An old SSM Agent does not set SSM_<name>: the values are absent, and nothing may run.
    result = _run(document, None, None)
    assert result.returncode == 64
    assert _calls(result) == []
