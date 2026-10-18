"""The pure parts of log search: grouping, cursor binding, and the per-line safety net."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import NotConfiguredError
from app.core.redaction import MARKER
from app.db.base import all_models
from app.inventory.models import Server
from app.logs import service
from app.logs.client import LokiUnavailableError, RawEntry
from app.logs.schemas import LogQueryRequest
from app.logs.service import _build, _Candidate, _fingerprint, _groups, _Resolved

all_models()

SID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def server(sid: uuid.UUID = SID, hostname: str = "web.internal") -> Server:
    return Server(id=sid, hostname=hostname)


def resolved(app: bool, system: bool, sid: uuid.UUID | None = None) -> _Resolved:
    return _Resolved(server(sid or uuid.uuid4()), app, system)


def candidate(labels: dict[str, str], line: str = "hello", ts: int = 5) -> _Candidate:
    return _Candidate(ts, RawEntry(ts, labels, line))


def labels(**over: str) -> dict[str, str]:
    base = {
        "server_id": str(SID),
        "source_type": "application",
        "service": "api",
        "level": "error",
        "hostname": "ATTACKER-SUPPLIED",
    }
    return {**base, **over}


def body(**over: object) -> LogQueryRequest:
    raw = {
        "from": "2026-01-15T08:00:00Z",
        "to": "2026-01-15T09:00:00Z",
        "server_ids": [str(SID)],
        **over,
    }
    return LogQueryRequest.model_validate(raw)


# --- time ---------------------------------------------------------------------


def test_nanosecond_conversion_is_exact_and_round_trips_to_microseconds() -> None:
    moment = datetime(2026, 1, 15, 8, 0, 0, 123_456, tzinfo=UTC)
    ns = service.to_ns(moment)
    assert ns == int(moment.timestamp()) * 10**9 + 123_456_000
    assert service.from_ns(ns + 789) == moment  # sub-microsecond is dropped, not rounded up


# --- grouping by what the caller may read ---------------------------------------


def test_servers_group_by_their_readable_source_types() -> None:
    both, app_only, sys_only = resolved(True, True), resolved(True, False), resolved(False, True)
    groups, dropped = _groups([both, app_only, sys_only], None)
    assert dropped == []
    by_types = {g.source_types: g.server_ids for g in groups}
    assert by_types[None] == (both.server.id,)
    assert by_types[("application",)] == (app_only.server.id,)
    assert by_types[("docker", "journald", "nginx", "php_fpm", "syslog")] == (sys_only.server.id,)


def test_a_requested_system_source_drops_servers_that_only_grant_application() -> None:
    both, app_only = resolved(True, True), resolved(True, False)
    groups, dropped = _groups([both, app_only], "nginx")
    assert [(g.source_types, g.server_ids) for g in groups] == [(("nginx",), (both.server.id,))]
    assert dropped == [app_only]


def test_a_requested_application_source_drops_system_only_servers() -> None:
    groups, dropped = _groups([resolved(False, True)], "application")
    assert groups == [] and len(dropped) == 1


def test_a_server_the_caller_can_read_nothing_on_never_gets_a_group() -> None:
    groups, dropped = _groups([resolved(False, False)], None)
    assert groups == [] and len(dropped) == 1


# --- the cursor is bound to the filter and the scope ----------------------------


def test_the_fingerprint_changes_with_every_filter_and_with_scope() -> None:
    groups, _ = _groups([resolved(True, True, SID)], None)
    base = _fingerprint(body(), groups)
    assert _fingerprint(body(), groups) == base
    for change in (
        {"contains": "x"},
        {"service": "api"},
        {"level": ["error"]},
        {"pattern_preset": "timeout"},
        {"environment": "staging"},
        {"application_id": str(uuid.uuid4())},
        {"direction": "forward"},
    ):
        assert _fingerprint(body(**change), groups) != base, change
    narrower, _ = _groups([resolved(True, False, SID)], None)
    assert _fingerprint(body(), narrower) != base  # the caller's reach changed


def test_the_fingerprint_ignores_the_time_range_and_the_limit() -> None:
    groups, _ = _groups([resolved(True, True, SID)], None)
    assert _fingerprint(body(limit=5, to="2026-01-15T10:00:00Z"), groups) == _fingerprint(
        body(), groups
    )


# --- one stored line to one safe entry ------------------------------------------


def test_a_line_is_redacted_and_carries_the_inventory_hostname_not_the_label() -> None:
    by_id = {str(SID): _Resolved(server(), True, False)}
    built = _build(candidate(labels(), "login password=hunter2 ok"), by_id)
    assert built is not None
    assert "hunter2" not in built.entry.line and MARKER in built.entry.line
    assert built.entry.hostname == "web.internal"
    assert built.entry.level == "error"


def test_a_line_outside_the_resolved_scope_is_dropped() -> None:
    by_id = {str(SID): _Resolved(server(), True, False)}
    other = labels(server_id=str(uuid.uuid4()))
    assert _build(candidate(other), by_id) is None
    assert _build(candidate({"source_type": "application"}), by_id) is None  # no server_id


def test_a_source_type_the_grant_does_not_cover_is_dropped_even_if_loki_returned_it() -> None:
    by_id = {str(SID): _Resolved(server(), True, False)}
    assert _build(candidate(labels(source_type="nginx")), by_id) is None
    assert _build(candidate(labels(source_type="journal")), by_id) is None


def test_hostile_labels_collapse_to_the_safe_fallbacks() -> None:
    by_id = {str(SID): _Resolved(server(), True, False)}
    built = _build(candidate(labels(service='x"} or {a="b', level="<script>")), by_id)
    assert built is not None
    assert (built.entry.service, built.entry.level) == ("other", "unknown")


def test_redaction_happens_before_the_cut_so_a_cut_cannot_expose_half_a_secret() -> None:
    by_id = {str(SID): _Resolved(server(), True, False)}
    secret = "S3CR3T" * 20
    # `token=` starts just inside the limit and its value runs past it.
    line = "x" * (service.MAX_LINE_CHARS - 12) + f" token={secret}"
    built = _build(candidate(labels(), line), by_id)
    assert built is not None
    assert built.entry.line_truncated is True
    assert "S3CR3T" not in built.entry.line
    assert len(built.entry.line) == service.MAX_LINE_CHARS


def test_the_entry_hash_is_over_the_redacted_line_and_stable() -> None:
    by_id = {str(SID): _Resolved(server(), True, False)}
    a = _build(candidate(labels(), "password=aaaa"), by_id)
    b = _build(candidate(labels(), "password=bbbb"), by_id)
    assert a is not None and b is not None
    assert a.line_hash == b.line_hash  # the secret is not in what is hashed


# --- failures are a closed set of answers ---------------------------------------


@pytest.mark.parametrize(
    ("reason", "status", "code"),
    [
        ("provider_timeout", 504, "provider_timeout"),
        ("provider_unreachable", 503, "provider_unavailable"),
        ("provider_error", 503, "provider_unavailable"),
        ("circuit_open", 503, "provider_unavailable"),
    ],
)
def test_loki_failures_translate_to_503_or_504_never_to_an_empty_page(
    reason: str, status: int, code: str
) -> None:
    error = service.translate(LokiUnavailableError(reason))  # type: ignore[arg-type]
    assert (error.status_code, error.code) == (status, code)


def test_not_configured_translates_to_not_configured() -> None:
    assert isinstance(service.translate(LokiUnavailableError("not_configured")), NotConfiguredError)
