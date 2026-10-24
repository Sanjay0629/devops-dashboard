"""The success policy (``OPEN-138``, AD-10, mandatory rule 8): pipeline success **and** health.

Until M5.5 no target can register a health check, so the only result recorded is the explicit
"none configured" one. These tests also pin what M5.5 must not be able to break: a result that
says it passed while one of its checks did not is not a pass.
"""

from __future__ import annotations

import pytest

from app.deployments import policy

PIPELINE_RESULTS = ("SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT", None)


def _result(*healthy: bool, passed: bool | None = None) -> dict[str, object]:
    checks = [{"name": f"c{i}", "healthy": h} for i, h in enumerate(healthy)]
    return {
        "policy": "all_must_pass",
        "configured": True,
        "checks": checks,
        "passed": all(healthy) if passed is None else passed,
    }


def test_none_configured_is_explicit_and_never_empty() -> None:
    result = policy.none_configured()
    assert result == {"policy": "all_must_pass", "configured": False, "checks": [], "passed": True}
    assert result != {}


def test_none_configured_returns_a_fresh_object_each_time() -> None:
    first = policy.none_configured()
    first["passed"] = False
    first["checks"].append("x")  # type: ignore[attr-defined]
    assert policy.none_configured()["passed"] is True
    assert policy.none_configured()["checks"] == []


def test_a_pipeline_success_with_nothing_configured_succeeds() -> None:
    assert policy.success("SUCCESS", policy.none_configured()) is True


@pytest.mark.parametrize("result", [r for r in PIPELINE_RESULTS if r != "SUCCESS"])
def test_only_a_pipeline_success_can_succeed(result: str | None) -> None:
    assert policy.success(result, policy.none_configured()) is False
    assert policy.success(result, _result(True, True)) is False


def test_unstable_is_not_success() -> None:
    assert policy.success("UNSTABLE", policy.none_configured()) is False


def test_every_check_must_pass() -> None:
    assert policy.success("SUCCESS", _result(True, True, True)) is True
    assert policy.success("SUCCESS", _result(True, False, True)) is False
    assert policy.success("SUCCESS", _result(False)) is False


def test_a_result_that_says_passed_over_a_failing_check_is_not_a_pass() -> None:
    assert policy.success("SUCCESS", _result(True, False, passed=True)) is False


def test_a_check_with_no_verdict_is_not_a_pass() -> None:
    result = _result(True)
    result["checks"] = [{"name": "api", "healthy": None}]
    assert policy.success("SUCCESS", result) is False
    result["checks"] = [{"name": "api"}]
    assert policy.success("SUCCESS", result) is False


@pytest.mark.parametrize(
    "health",
    [
        None,
        {},
        [],
        "passed",
        {"passed": True},  # no policy: not something this code wrote
        {"policy": "all_must_pass", "passed": True},  # no checks list
        {"policy": "any_may_pass", "configured": True, "checks": [], "passed": True},
        {"policy": "all_must_pass", "configured": True, "checks": [], "passed": None},
        {"policy": "all_must_pass", "configured": True, "checks": [], "passed": 1},
        {"policy": "all_must_pass", "configured": True, "checks": [], "passed": "true"},
        {"policy": "all_must_pass", "configured": True, "checks": "none", "passed": True},
    ],
)
def test_anything_that_is_not_a_well_formed_passing_result_is_not_a_pass(health: object) -> None:
    assert policy.success("SUCCESS", health) is False  # type: ignore[arg-type]


def test_a_configured_result_with_no_checks_cannot_claim_to_pass() -> None:
    """``configured: true`` means checks were registered; none having run is not a pass."""
    health = {"policy": "all_must_pass", "configured": True, "checks": [], "passed": True}
    assert policy.success("SUCCESS", health) is False


def test_the_statement_the_page_shows_is_fixed_text() -> None:
    assert "No post-deployment health checks are configured" in policy.NONE_CONFIGURED_STATEMENT
    assert "only that the pipeline succeeded" in policy.NONE_CONFIGURED_STATEMENT
