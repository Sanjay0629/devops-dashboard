"""The closed sets of the deployment lifecycle (``OPEN-137``): failure reasons and their inputs.

The reasons are written out here from API_CONTRACTS §21.3, so a code added without a decision is a
failing test. Nothing Jenkins says reaches a column: every mapping below goes from a *closed* input
to a *closed* output.
"""

from __future__ import annotations

import typing

import pytest

from app.deployments import codes
from app.deployments.jenkins import JenkinsReason

DOCUMENTED = {
    "PERMISSION_REVOKED",
    "APPROVAL_REQUIRED",
    "TARGET_VALIDATION_FAILED",
    "TARGET_CHANGED",
    "REF_NOT_PERMITTED",
    "NOT_CONFIGURED",
    "JENKINS_UNAVAILABLE",
    "JENKINS_AUTHENTICATION",
    "JOB_NOT_FOUND",
    "TRIGGER_OUTCOME_UNKNOWN",
    "QUEUE_ITEM_LOST",
    "PIPELINE_FAILED",
    "PIPELINE_UNSTABLE",
    "PIPELINE_ABORTED",
    "PIPELINE_NOT_BUILT",
    "DEPLOYMENT_TIMED_OUT",
    "EXPIRED_BEFORE_START",
}


def test_the_failure_reasons_are_exactly_the_documented_set() -> None:
    assert set(codes.FAILURE_REASONS) == DOCUMENTED
    assert len(codes.FAILURE_REASONS) == len(set(codes.FAILURE_REASONS))


def test_health_check_failure_is_not_a_code_until_m55() -> None:
    assert "HEALTH_CHECKS_FAILED" not in codes.FAILURE_REASONS


def test_every_reason_fits_the_column_and_is_shouted_snake_case() -> None:
    for reason in codes.FAILURE_REASONS:
        assert codes.is_failure_reason(reason)
        assert len(reason) <= 64
        assert reason == reason.upper() and " " not in reason


@pytest.mark.parametrize(
    "value", ["", "pipeline_failed", "HEALTH_CHECKS_FAILED", "TIMED_OUT", None]
)
def test_anything_else_is_not_a_failure_reason(value: object) -> None:
    assert not codes.is_failure_reason(value)  # type: ignore[arg-type]


# --- what a Jenkins result becomes -----------------------------------------------------------


def test_the_pipeline_results_are_the_five_jenkins_reports() -> None:
    assert set(codes.PIPELINE_RESULTS) == {"SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT"}


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        ("FAILURE", "PIPELINE_FAILED"),
        ("UNSTABLE", "PIPELINE_UNSTABLE"),
        ("ABORTED", "PIPELINE_ABORTED"),
        ("NOT_BUILT", "PIPELINE_NOT_BUILT"),
    ],
)
def test_a_pipeline_that_did_not_succeed_has_its_own_failure_reason(
    result: str, reason: str
) -> None:
    assert codes.failure_for_result(result) == reason


def test_a_successful_pipeline_has_no_failure_reason() -> None:
    assert codes.failure_for_result("SUCCESS") is None


@pytest.mark.parametrize("result", ["", "success", "PENDING", "Finished: FAILURE", None])
def test_a_result_jenkins_does_not_define_is_refused_not_guessed(result: object) -> None:
    with pytest.raises(ValueError, match="pipeline result"):
        codes.failure_for_result(result)  # type: ignore[arg-type]


# --- what a failed trigger becomes -----------------------------------------------------------

JENKINS_REASONS = set(typing.get_args(JenkinsReason))


def test_every_jenkins_reason_is_classified_exactly_once() -> None:
    """A reason added to the client without deciding what a trigger does about it fails here."""
    classified = [*codes.TRIGGER_FAILURE, *codes.TRIGGER_RECONCILE_ONLY]
    assert set(classified) == JENKINS_REASONS
    assert len(classified) == len(set(classified))


@pytest.mark.parametrize(
    ("reason", "failure"),
    [
        ("not_configured", "NOT_CONFIGURED"),
        ("provider_unreachable", "JENKINS_UNAVAILABLE"),
        ("circuit_open", "JENKINS_UNAVAILABLE"),
        ("destination_refused", "JENKINS_UNAVAILABLE"),
        ("provider_timeout", "JENKINS_UNAVAILABLE"),
        ("provider_error", "JENKINS_UNAVAILABLE"),
        ("auth_failed", "JENKINS_AUTHENTICATION"),
        ("crumb_rejected", "JENKINS_AUTHENTICATION"),
        ("job_not_found", "JOB_NOT_FOUND"),
    ],
)
def test_a_trigger_that_definitely_did_not_act_is_a_terminal_failure(
    reason: str, failure: str
) -> None:
    assert codes.trigger_failure(reason, may_have_acted=False) == failure
    assert failure in codes.FAILURE_REASONS


@pytest.mark.parametrize("reason", sorted(JENKINS_REASONS))
def test_a_trigger_that_may_have_acted_is_never_a_terminal_failure(reason: str) -> None:
    """The safe side: an ambiguous answer is reconciled, so the trigger is never repeated."""
    assert codes.trigger_failure(reason, may_have_acted=True) is None


def test_trigger_outcome_unknown_is_reconciled_whatever_the_flag_says() -> None:
    assert codes.trigger_failure("trigger_outcome_unknown", may_have_acted=False) is None
    assert codes.trigger_failure("trigger_outcome_unknown", may_have_acted=True) is None


def test_an_unknown_reason_fails_closed() -> None:
    with pytest.raises(ValueError, match="Jenkins reason"):
        codes.trigger_failure("a sentence from jenkins", may_have_acted=False)


# --- reconciliation and the statuses a reason ends in ----------------------------------------


def test_reconciliation_is_required_where_a_build_may_exist_that_dcp_cannot_see() -> None:
    assert set(codes.RECONCILIATION_REASONS) == {
        "TRIGGER_OUTCOME_UNKNOWN",
        "QUEUE_ITEM_LOST",
        "DEPLOYMENT_TIMED_OUT",
    }
    assert codes.needs_reconciliation("DEPLOYMENT_TIMED_OUT")
    assert not codes.needs_reconciliation("PIPELINE_FAILED")
    assert not codes.needs_reconciliation("JENKINS_UNAVAILABLE")  # nothing was sent


def test_a_reason_that_means_nothing_was_sent_never_asks_for_reconciliation() -> None:
    for reason in (
        "PERMISSION_REVOKED",
        "APPROVAL_REQUIRED",
        "TARGET_VALIDATION_FAILED",
        "TARGET_CHANGED",
        "REF_NOT_PERMITTED",
        "NOT_CONFIGURED",
        "JENKINS_UNAVAILABLE",
        "JENKINS_AUTHENTICATION",
        "JOB_NOT_FOUND",
        "EXPIRED_BEFORE_START",
    ):
        assert not codes.needs_reconciliation(reason), reason


def test_every_failure_reason_ends_in_failed_there_is_no_timed_out_status() -> None:
    for reason in codes.FAILURE_REASONS:
        assert codes.terminal_status_for(reason) == "FAILED"


def test_an_unknown_reason_has_no_terminal_status() -> None:
    with pytest.raises(ValueError, match="failure reason"):
        codes.terminal_status_for("SOMETHING_NEW")


def test_the_reasons_the_reaper_writes_for_a_deadline() -> None:
    assert codes.EXPIRED_BEFORE_START in codes.FAILURE_REASONS
    assert codes.DEPLOYMENT_TIMED_OUT in codes.FAILURE_REASONS


# --- the other closed enums the row carries --------------------------------------------------


def test_kinds_and_approval_statuses() -> None:
    assert codes.KINDS == ("DEPLOY", "ROLLBACK")
    assert codes.APPROVAL_STATUSES == ("NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED")
    assert "EXPIRED" not in codes.APPROVAL_STATUSES  # an operations value, not a deployments one
