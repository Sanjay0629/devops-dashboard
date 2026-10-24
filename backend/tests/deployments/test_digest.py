"""The target digest (``OPEN-135``, ``OPEN-137``): what the worker compares before it triggers.

A deployment records the digest of the pipeline and parameter template the requester was shown.
If an administrator edits the target before the worker triggers, the digest differs and nothing is
sent (``TARGET_CHANGED``). The digest must therefore change for every edit that changes what
Jenkins would receive, and for nothing else.
"""

from __future__ import annotations

import re

import pytest

from app.deployments.digest import target_digest

TEMPLATE = {
    "fixed": {"TARGET": "staging"},
    "slots": {"GIT_REF": "ref", "DCP_DEPLOYMENT_ID": "deployment_id"},
}


def test_it_is_a_sha256_hex_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", target_digest("team/deploy", TEMPLATE))


def test_it_is_stable() -> None:
    assert target_digest("team/deploy", TEMPLATE) == target_digest("team/deploy", dict(TEMPLATE))
    # Known answer: a change to the canonical form is a change to what every stored digest means.
    # sha256 of {"parameter_template":{},"pipeline_reference":"team/deploy"}, computed by hand.
    assert target_digest("team/deploy", {}) == (
        "112c54158311055edf91b6c5cbee1413b616b7776cb1c60fc9c1a42bbfadd72d"
    )


def test_key_order_does_not_matter_because_jsonb_does_not_keep_it() -> None:
    forward = {"fixed": {"A": "1", "B": "2"}, "slots": {"X": "ref", "Y": "environment"}}
    backward = {"slots": {"Y": "environment", "X": "ref"}, "fixed": {"B": "2", "A": "1"}}
    assert target_digest("a/b", forward) == target_digest("a/b", backward)


@pytest.mark.parametrize(
    "changed",
    [
        {"fixed": {"TARGET": "production"}, "slots": TEMPLATE["slots"]},  # a fixed value
        {"fixed": {"TARGET2": "staging"}, "slots": TEMPLATE["slots"]},  # a name
        {"fixed": {}, "slots": TEMPLATE["slots"]},  # a removed parameter
        {"fixed": TEMPLATE["fixed"], "slots": {"GIT_REF": "ref"}},  # a removed slot
        {"fixed": TEMPLATE["fixed"], "slots": {**TEMPLATE["slots"], "WHO": "requested_by"}},
        {"fixed": TEMPLATE["fixed"], "slots": {"GIT_REF": "environment", "X": "deployment_id"}},
        {},
    ],
)
def test_any_change_to_what_jenkins_would_receive_changes_the_digest(
    changed: dict[str, object],
) -> None:
    assert target_digest("team/deploy", changed) != target_digest("team/deploy", TEMPLATE)


def test_a_different_pipeline_changes_the_digest() -> None:
    assert target_digest("team/deploy", TEMPLATE) != target_digest("team/deploy-2", TEMPLATE)
    assert target_digest("team/deploy", TEMPLATE) != target_digest("team/Deploy", TEMPLATE)


def test_moving_text_between_the_two_inputs_cannot_collide() -> None:
    """A concatenation would let a value cross the boundary; a structured encoding cannot."""
    assert target_digest("a/b", {"fixed": {"X": "1"}}) != target_digest(
        "a/b1", {"fixed": {"X": ""}}
    )
    assert target_digest("a", {"fixed": {"b": "c"}}) != target_digest("a/b", {"fixed": {"c": ""}})


def test_a_number_is_not_its_string() -> None:
    assert target_digest("a/b", {"fixed": {"N": "1"}}) != target_digest("a/b", {"fixed": {"N": 1}})


def test_non_ascii_text_is_encoded_deterministically() -> None:
    first = target_digest("a/b", {"fixed": {"NAME": "naïve ✓"}})
    assert first == target_digest("a/b", {"fixed": {"NAME": "naïve ✓"}})
    assert first != target_digest("a/b", {"fixed": {"NAME": "naive ✓"}})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_value_json_cannot_represent_is_refused_not_hashed(value: float) -> None:
    with pytest.raises(ValueError):
        target_digest("a/b", {"fixed": {"X": value}})


def test_it_does_not_mutate_its_input() -> None:
    template = {"fixed": {"B": "2", "A": "1"}, "slots": {}}
    before = repr(template)
    target_digest("a/b", template)
    assert repr(template) == before
