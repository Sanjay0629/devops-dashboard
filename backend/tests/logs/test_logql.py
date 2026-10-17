"""LogQL rendering (AD-05): every filter combination, escaping, and the injection suite.

The property under test is not "this string looks right" but "nothing a request
supplies can change the *shape* of the query". Where a value is free-form
(``contains``), the test parses the rendered literal back and requires that it
is exactly one string, consumed to the end, equal to what was asked for.
"""

from __future__ import annotations

import itertools
import re
import uuid

import pytest

from app.logs import guards
from app.logs.logql import LogFilter, ingestion_query, log_query, quote, stream_selector
from app.logs.presets import BY_ID

S1 = uuid.UUID("11111111-1111-4111-8111-111111111111")
S2 = uuid.UUID("22222222-2222-4222-8222-222222222222")
APP = uuid.UUID("33333333-3333-4333-8333-333333333333")


def read_literal(text: str) -> tuple[str, str]:
    """Parse one LogQL double-quoted literal off the front; return (value, rest)."""
    assert text[0] == '"'
    out: list[str] = []
    i = 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            out.append(text[i + 1])
            i += 2
        elif c == '"':
            return "".join(out), text[i + 1 :]
        else:
            out.append(c)
            i += 1
    raise AssertionError("unterminated literal")


# --- exact renderings ---------------------------------------------------------


def test_the_minimal_query_is_one_server_matcher() -> None:
    assert stream_selector(LogFilter(server_ids=(S1,))) == f'{{server_id="{S1}"}}'


def test_several_servers_become_one_anchored_alternation() -> None:
    assert stream_selector(LogFilter(server_ids=(S1, S2))) == f'{{server_id=~"{S1}|{S2}"}}'


def test_duplicate_servers_are_collapsed() -> None:
    assert stream_selector(LogFilter(server_ids=(S1, S1))) == f'{{server_id="{S1}"}}'


def test_a_full_query_renders_in_a_fixed_order() -> None:
    f = LogFilter(
        server_ids=(S1,),
        source_types=("nginx", "docker"),
        environment="staging",
        application_id=APP,
        service="web",
        levels=("error", "critical"),
        contains="boom",
        preset="http_5xx",
    )
    assert log_query(f) == (
        f'{{server_id="{S1}", environment="staging", application_id="{APP}", service="web", '
        'source_type=~"nginx|docker", level=~"error|critical"}'
        ' |= "boom" |~ "(?i)HTTP/[0-9.]+\\" 5[0-9]{2} |\\"status\\": ?5[0-9]{2}\\\\b"'
    )


def test_ingestion_query_is_a_per_server_count() -> None:
    assert ingestion_query([S1, S2], 900) == (
        f'sum by (server_id) (count_over_time({{server_id=~"{S1}|{S2}"}}[900s]))'
    )


# --- every combination of the optional filters --------------------------------

OPTIONAL = {
    "environment": "production",
    "application_id": APP,
    "service": "api.service",
    "source_types": ("application",),
    "levels": ("warn",),
    "contains": "needle",
    "preset": "timeout",
}


@pytest.mark.parametrize(
    "chosen",
    [
        subset
        for size in range(len(OPTIONAL) + 1)
        for subset in itertools.combinations(OPTIONAL, size)
    ],
    ids=lambda subset: "+".join(subset) or "bare",
)
def test_each_filter_appears_exactly_when_it_was_given(chosen: tuple[str, ...]) -> None:
    f = LogFilter(server_ids=(S1, S2), **{key: OPTIONAL[key] for key in chosen})
    query = log_query(f)

    selector, _, filters = query.partition("} ")
    selector += "}"
    assert selector.startswith("{server_id=~")
    for label, key in (
        ("environment=", "environment"),
        ("application_id=", "application_id"),
        ("service=", "service"),
        ("source_type=", "source_types"),
        ("level=", "levels"),
    ):
        assert (label in selector) == (key in chosen), (label, query)
    assert ("|= " in query) == ("contains" in chosen)
    assert ("|~ " in query) == ("preset" in chosen)
    # Exactly one stream selector, and it closes before any line filter.
    assert query.count("{") == query.count("}") == 1 + query.count("{2}")
    assert selector.count("{") == 1
    assert filters == "" or filters.startswith(("|=", "|~"))


# --- contains: escaping, proven by parsing the literal back --------------------

HOSTILE = [
    "plain",
    'q"uo"te',
    "back\\slash",
    "ends with backslash\\",
    '\\" already looks escaped',
    '" } or {server_id=~".+"} |= "',
    "`backtick` {braces} | pipe ~ tilde",
    "$(rm -rf /) ; DROP TABLE logs",
    "emoji \U0001f525 and \u00e9 and \u2028 and \u0085",
    "a" * 256,
]


@pytest.mark.parametrize("needle", HOSTILE)
def test_contains_is_one_literal_equal_to_the_input(needle: str) -> None:
    query = log_query(LogFilter(server_ids=(S1,), contains=needle))
    _, _, rest = query.partition("} |= ")
    value, remainder = read_literal(rest)
    assert value == needle
    assert remainder == ""
    # And the selector in front of it is untouched: one server, nothing added.
    assert query.startswith(f'{{server_id="{S1}"}} |= "')


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\tb", "a\x00b", "a\x1bb", "a\x7fb"])
def test_contains_with_a_control_character_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        quote(bad)
    with pytest.raises(ValueError):
        log_query(LogFilter(server_ids=(S1,), contains=bad))


# --- the injection suite: every other field is closed --------------------------

INJECTIONS = ['x"} or {a="b', 'x" |= "', "x\n", "a b", "../x", "{x}", "x" * 64, "-lead", ""]


@pytest.mark.parametrize("value", INJECTIONS)
def test_a_service_outside_the_label_shape_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(S1,), service=value))


@pytest.mark.parametrize("value", [*INJECTIONS, "Production", "prod"])
def test_an_environment_outside_the_closed_set_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(S1,), environment=value))


@pytest.mark.parametrize("value", [*INJECTIONS, "file", "journal", "NGINX"])
def test_a_source_type_outside_the_closed_set_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(S1,), source_types=(value,)))


@pytest.mark.parametrize("value", [*INJECTIONS, "warning", "notice", "trace", "ERROR"])
def test_a_level_outside_the_closed_set_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(S1,), levels=(value,)))


@pytest.mark.parametrize("value", [*INJECTIONS, "http_4xx", ".*", "HTTP_5XX"])
def test_a_preset_outside_the_catalogue_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        log_query(LogFilter(server_ids=(S1,), preset=value))


@pytest.mark.parametrize("value", [*INJECTIONS, "not-a-uuid", 'a"|b'])
def test_a_server_that_is_not_a_uuid_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(value,)))  # type: ignore[arg-type]


def test_an_empty_list_of_a_closed_filter_is_refused_not_dropped() -> None:
    """Dropping an empty ``level`` would widen the query to every level."""
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(S1,), levels=()))
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(S1,), source_types=()))


def test_a_query_with_no_server_is_refused() -> None:
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=()))


def test_the_server_cap_is_inclusive() -> None:
    ids = tuple(uuid.uuid4() for _ in range(guards.MAX_RESOLVED_SERVERS))
    assert stream_selector(LogFilter(server_ids=ids)).count("|") == len(ids) - 1
    with pytest.raises(ValueError):
        stream_selector(LogFilter(server_ids=(*ids, uuid.uuid4())))


@pytest.mark.parametrize("window", [0, -1, 7 * 24 * 3600 + 1])
def test_the_ingestion_window_is_bounded(window: int) -> None:
    with pytest.raises(ValueError):
        ingestion_query([S1], window)


def test_every_preset_renders_as_a_single_literal() -> None:
    for preset in BY_ID.values():
        query = log_query(LogFilter(server_ids=(S1,), preset=preset.id))
        value, rest = read_literal(query.partition("} |~ ")[2])
        assert (value, rest) == (preset.regex, "")


def test_no_rendered_query_contains_a_raw_newline() -> None:
    query = log_query(LogFilter(server_ids=(S1,), contains="x", preset="timeout"))
    assert re.search(r"[\r\n]", query) is None
