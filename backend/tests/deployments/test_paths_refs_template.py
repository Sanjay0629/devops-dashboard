"""The pure core of M5.1: job paths, refs, ref patterns and the parameter template."""

from __future__ import annotations

import re
import uuid

import pytest
from pydantic import ValidationError

from app.deployments import paths, refs, template
from app.deployments.template import ParameterTemplate, ref_slot_problem, render_parameters

# --- job paths ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["a", "team/svc", "A.b_c-1/x", "a/b/c/d/e", "x" * 64])
def test_job_paths_that_are_accepted(value: str) -> None:
    assert paths.validate_job_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "", ".", "..", "../x", "a/../b", ".hidden", "a/.b", "-x", "a//b", "a/", "/a", "a b",
        "a?x=1", "a#f", "a%2e", "a\nb", "a\n", "a/b/c/d/e/f", "x" * 65, "ä", "a;b", "a\\b",
    ],
)  # fmt: skip
def test_job_paths_that_are_refused(value: str) -> None:
    with pytest.raises(paths.InvalidJobPathError):
        paths.validate_job_path(value)


def test_the_error_names_the_rule_and_not_the_value() -> None:
    with pytest.raises(paths.InvalidJobPathError) as raised:
        paths.validate_job_path("../secret-looking-value")
    assert "secret-looking-value" not in str(raised.value)


def test_the_url_path_is_built_from_segments() -> None:
    assert paths.job_url_path("team/svc") == "/job/team/job/svc"
    assert paths.job_url_path("single") == "/job/single"


def test_the_url_builder_revalidates() -> None:
    with pytest.raises(paths.InvalidJobPathError):
        paths.job_url_path("a/../b")


def test_the_regex_and_the_pattern_agree_on_the_edges() -> None:
    """The ``CHECK`` uses ``JOB_PATH_PATTERN``; Python uses ``fullmatch`` of the same grammar."""
    sql_style = re.compile(paths.JOB_PATH_PATTERN)
    for value in ("a", "a/b", "a\n"):
        python_ok = True
        try:
            paths.validate_job_path(value)
        except paths.InvalidJobPathError:
            python_ok = False
        # ``$`` would admit a trailing newline in Python; ``fullmatch`` does not, so Python is
        # the stricter of the two and the database's POSIX ``$`` is not looser than it.
        if python_ok:
            assert sql_style.match(value)


# --- refs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    ["main", "release/1.2.3", "feature/x-y_z", "v1.0", "0123456", "a" * 40, "a" * 200],
)
def test_valid_refs(ref: str) -> None:
    assert refs.is_valid_ref(ref)


@pytest.mark.parametrize(
    "ref",
    [
        "", "-rf", "-", "/main", "main/", "main.", "a..b", "a//b", "a b", "a\nb", "main\n",
        "a@{1}", "a~1", "a^", "a:b", "a?", "a*", "a[", "a\\b", "x.lock", "a" * 201, "é", None, 7,
    ],
)  # fmt: skip
def test_invalid_refs(ref: object) -> None:
    assert not refs.is_valid_ref(ref)


@pytest.mark.parametrize(
    ("patterns", "ref", "expected"),
    [
        (["^main$"], "main", True),
        (["main"], "main", True),  # fullmatch: no anchors needed
        (["main"], "main-evil", False),  # ...and none can be bypassed
        (["^main$"], "main-evil", False),
        (["^release/.+$"], "release/1.2", True),
        (["^release/.+$"], "release/", False),  # not a valid ref at all
        (["^release/.+$"], "xrelease/1.2", False),
        (["release/.+"], "release/../main", False),  # the ref grammar refuses it first
        (["^main$"], "main\n", False),  # the Python `$`-before-newline edge
        (["^main$", "^dev$"], "dev", True),
        (["^main$"], "dev", False),
        ([], "main", False),  # empty patterns permit nothing
        ([".*"], "main", True),
        ([".*"], "-rf", False),  # a permissive pattern still meets the ref grammar
        (["["], "main", False),  # a stored pattern that does not compile fails closed
        (["^main$"], None, False),
    ],
)
def test_ref_permitted(patterns: list[str], ref: object, expected: bool) -> None:
    assert refs.ref_permitted(patterns, ref) is expected


@pytest.mark.parametrize(
    "pattern",
    [
        "^main$", "^release/.+$", "^v[0-9]+\\.[0-9]+$", "(?:a|b)", "(a|b)", "^[a-z]{1,10}$",
        "[]]", "[^]]", "\\(", "a\\)+", "[)+]",
    ],
)  # fmt: skip
def test_patterns_that_may_be_stored(pattern: str) -> None:
    assert refs.validate_pattern(pattern) == pattern


@pytest.mark.parametrize(
    "pattern",
    [
        "", "x" * 201, "ünï", "a\nb", "(", "[", "(?=a)", "(?!a)", "(?<=a)", "(?i)a", "(?P<n>a)",
        "(a)\\1", "\\g<1>", "(a)+", "(a)*", "(a)?", "(a){2}", "(a+)+", "(?:a)+", "((a))+",
    ],
)  # fmt: skip
def test_patterns_that_are_refused(pattern: str) -> None:
    with pytest.raises(refs.InvalidPatternError):
        refs.validate_pattern(pattern)


def test_the_pattern_error_never_echoes_the_pattern() -> None:
    with pytest.raises(refs.InvalidPatternError) as raised:
        refs.validate_pattern("(?=hunter2)")
    assert "hunter2" not in str(raised.value)


def test_a_group_quantifier_inside_a_class_or_after_an_escape_is_not_a_group() -> None:
    assert refs.validate_pattern("[(]+")  # '(' is a class member
    assert refs.validate_pattern("\\)+")  # an escaped paren, not a group close


# --- the parameter template ---------------------------------------------------------------


def test_a_template_defaults_to_empty() -> None:
    parsed = ParameterTemplate.model_validate({})
    assert parsed.fixed == {} and parsed.slots == {}


def test_a_template_with_fixed_values_and_slots() -> None:
    parsed = ParameterTemplate.model_validate(
        {"fixed": {"TARGET": "staging"}, "slots": {"GIT_REF": "ref", "ID": "deployment_id"}}
    )
    assert parsed.ref_slots == ["GIT_REF"]


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": 1},
        {"fixed": {"1BAD": "x"}},
        {"fixed": {"has space": "x"}},
        {"fixed": {"A": 1}},
        {"fixed": {"A": "x" * 257}},
        {"fixed": {"A": "line\nbreak"}},
        {"fixed": {"A": "tab\there"}},
        {"slots": {"A": "request_body"}},  # not a slot source
        {"slots": {"A": "url"}},
        {"fixed": {"A": "x"}, "slots": {"A": "ref"}},  # a name once only
        {"fixed": {f"P{i}": "x" for i in range(21)}},
        {"fixed": {"PASSWORD": "hunter2hunter2"}},  # a secret-shaped name masks its value
        {"fixed": {"NOTE": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"}},
        {"fixed": {"NOTE": "AKIAIOSFODNN7EXAMPLE"}},
    ],
)
def test_templates_that_are_refused(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ParameterTemplate.model_validate(body)


def test_twenty_parameters_are_allowed() -> None:
    ParameterTemplate.model_validate({"fixed": {f"P{i}": "x" for i in range(20)}})


def test_the_slot_sources_are_exactly_the_documented_set() -> None:
    assert set(template.SLOT_SOURCES) == {"ref", "deployment_id", "environment", "requested_by"}


@pytest.mark.parametrize(
    ("slots", "patterns", "ok"),
    [
        ({"R": "ref"}, ["^main$"], True),
        ({}, [], True),
        ({"E": "environment"}, [], True),
        ({}, ["^main$"], False),  # a ref is validated that never reaches Jenkins
        ({"R": "ref", "S": "ref"}, ["^main$"], False),
        ({"R": "ref"}, [], False),  # a ref slot with nothing permitted
    ],
)
def test_the_ref_slot_rule(slots: dict[str, str], patterns: list[str], ok: bool) -> None:
    parsed = ParameterTemplate.model_validate({"slots": slots})
    assert (ref_slot_problem(parsed, patterns) is None) is ok


def test_rendering_fills_only_the_declared_slots() -> None:
    deployment, user = uuid.uuid4(), uuid.uuid4()
    rendered = render_parameters(
        {
            "fixed": {"TARGET": "staging"},
            "slots": {
                "GIT_REF": "ref",
                "DEP": "deployment_id",
                "ENV": "environment",
                "WHO": "requested_by",
            },
        },
        ref="release/1.0",
        deployment_id=deployment,
        environment="staging",
        requested_by=user,
    )
    assert rendered == {
        "TARGET": "staging",
        "GIT_REF": "release/1.0",
        "DEP": str(deployment),
        "ENV": "staging",
        "WHO": str(user),
    }


def test_nothing_but_the_template_can_enter_the_result() -> None:
    """The renderer takes four values and a template; there is no mapping to smuggle a name in."""
    rendered = render_parameters(
        {"slots": {"ENV": "environment"}},
        ref="main",  # supplied but the template has no ref slot: it must not appear
        deployment_id=uuid.uuid4(),
        environment="staging",
        requested_by=uuid.uuid4(),
    )
    assert rendered == {"ENV": "staging"}


def test_a_ref_slot_without_a_ref_is_an_error_not_an_empty_parameter() -> None:
    with pytest.raises(ValueError, match="validated ref"):
        render_parameters(
            {"slots": {"R": "ref"}},
            ref=None,
            deployment_id=uuid.uuid4(),
            environment="staging",
            requested_by=uuid.uuid4(),
        )


def test_a_malformed_stored_template_is_refused_at_render_time() -> None:
    with pytest.raises(ValidationError):
        render_parameters(
            {"slots": {"A": "anything"}},
            ref=None,
            deployment_id=uuid.uuid4(),
            environment="staging",
            requested_by=uuid.uuid4(),
        )


def test_every_canary_line_in_the_corpus_is_refused_as_a_fixed_value() -> None:
    """The project's canary corpus (the lines redaction exists for) cannot be stored in a template.

    The template is returned to administrators and sits in the database, so a secret has no
    business there; this plants every canary shape the project knows and expects each refused.
    """
    import json
    from pathlib import Path

    corpus = json.loads(
        (Path(__file__).parents[3] / "infra/compose/sample-logs/canaries.json").read_text("utf-8")
    )
    refused = 0
    for canary in corpus:
        canary_lines = canary.get("lines") or [canary["line"]]
        if len(canary_lines) > 1:
            # A multi-line PEM key is redacted only as a block, line by line it is not
            # (PROGRESS issue 92, a known and pinned limitation).
            continue
        planted = canary_lines[0].replace("<<SECRET>>", canary["secret"])
        with pytest.raises(ValidationError):
            ParameterTemplate.model_validate({"fixed": {"NOTE": planted}})
        refused += 1
    assert refused >= 7, "the corpus should supply several single-line canaries"
