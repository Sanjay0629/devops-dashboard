"""§19.1, §19.2 and §16.7 end to end: real sessions, grants, scope SQL and rows.

Loki is a stand-in (``tests.logs.fake_loki``) that *evaluates the LogQL it is
sent*, so a selector that is wrong or too wide returns the wrong lines instead of
passing. What this file establishes is how the **portal** treats an answer -- who
may ask, which servers and source types the answer may be about, what is said when
Loki cannot answer, and that paging neither repeats nor skips a line. That a real
Loki accepts the queries is the smoke test's job (step 18f), not this file's.

The authorisation cases are made by narrowing a live caller's grants and watching
the answer narrow, not by reading the handler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core import ratelimit
from app.core.config import get_settings
from app.core.redaction import MARKER
from app.core.redis import get_redis
from app.logs import router as logs_router
from app.logs import service as logs_service
from app.logs.client import LokiClient, reset_breaker
from app.logs.service import to_ns
from app.metrics.client import CircuitBreaker
from tests.integration.inventory_helpers import (
    STAGING_SCOPE,
    account_id,
    call,
    grant,
    register,
    revoke_all,
    user_id,
)
from tests.integration.test_auth_flow import (  # noqa: F401 — fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)
from tests.logs.fake_loki import FakeLoki
from tests.logs.test_logql import read_literal

QUERY = "/api/v1/logs/query"
SOURCES = "/api/v1/logs/sources"
RANGE = "/loki/api/v1/query_range"
INSTANT = "/loki/api/v1/query"
READ_ONLY_PATH = "/api/v1/admin/settings/read-only-mode"

PUBLISHED_LIMIT = logs_router.QUERY_RATE_LIMIT
PUBLISHED_WINDOW = logs_router.QUERY_RATE_WINDOW_SECONDS

NOW = datetime.now(UTC).replace(microsecond=0)
NOW_NS = to_ns(NOW)
SEC = 10**9
FROM = (NOW - timedelta(hours=1)).isoformat()
TO = (NOW + timedelta(minutes=1)).isoformat()


@dataclass
class Fleet:
    client: httpx.AsyncClient
    engine: Engine
    caller: uuid.UUID
    fake: FakeLoki
    monkeypatch: pytest.MonkeyPatch
    #: name -> (server id, hostname)
    servers: dict[str, tuple[str, str]]

    def id(self, name: str) -> str:
        return self.servers[name][0]

    def put(
        self,
        name: str,
        text_: str,
        *,
        age: int = 30,
        source: str = "application",
        service: str = "api",
        level: str = "info",
        ts: int | None = None,
        **extra: str,
    ) -> int:
        """Store one line for ``name``, ``age`` seconds old (or at ``ts``)."""
        stamp = NOW_NS - age * SEC if ts is None else ts
        labels = {
            "environment": "staging",
            "server_id": self.id(name),
            "hostname": self.servers[name][1],
            "service": service,
            "source_type": source,
            "level": level,
        }
        labels.update(extra)
        self.fake.add(stamp, text_, **labels)
        return stamp

    async def search(self, **body: Any) -> httpx.Response:
        payload = {"from": FROM, "to": TO, **body}
        payload = {key: value for key, value in payload.items() if value is not ...}
        return await call(self.client, "POST", QUERY, payload)

    def loki_log_queries(self) -> list[str]:
        return [q for path, q, _ in self.fake.requests if path == RANGE]


@pytest.fixture
async def fleet(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Fleet:
    """Two staging servers and a production one, an Administrator, and a Loki."""
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    account = account_id(migrated)

    servers: dict[str, tuple[str, str]] = {}
    for name, display, environment in (
        ("web", "A web", "staging"),
        ("db", "B db", "staging"),
        ("prod", "C prod", "production"),
    ):
        response = await register(client, account, display_name=display, environment=environment)
        assert response.status_code == 201, response.text
        servers[name] = (response.json()["id"], response.json()["hostname"])

    monkeypatch.setenv("DCP_LOKI_URL", "http://loki.invalid:3100")
    get_settings.cache_clear()
    reset_breaker()
    fake = FakeLoki()

    def make_client() -> LokiClient:
        return LokiClient(
            settings=get_settings(), transport=fake.transport(), circuit=CircuitBreaker()
        )

    monkeypatch.setattr(logs_service, "make_client", make_client)
    # The rate limit is per identity in Redis and the same test user is reused, so
    # thirty calls across the file would trip it. The limit has its own test.
    monkeypatch.setattr(logs_router, "QUERY_RATE_LIMIT", 100_000)
    return Fleet(client, migrated, caller, fake, monkeypatch, servers)


def narrow(fleet: Fleet, role: str, scope: str | None = None) -> None:
    revoke_all(fleet.engine, fleet.caller)
    if scope is None:
        grant(fleet.engine, fleet.caller, role)
    else:
        grant(fleet.engine, fleet.caller, role, scope)


def audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM audit_events")).scalar_one())


def denials(engine: Engine, actor: uuid.UUID) -> list[str]:
    with engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT sanitized_metadata->>'permission' FROM audit_events"
                    " WHERE action_type = 'AUTHZ_DENIED' AND actor_id = :a"
                ),
                {"a": actor},
            )
        ]


# --- the happy path --------------------------------------------------------------


async def test_a_search_returns_redacted_lines_newest_first_with_the_inventory_hostname(
    fleet: Fleet,
) -> None:
    fleet.put("web", "older", age=60)
    fleet.put("web", "login password=hunter2 done", age=30, level="error")
    fleet.put("web", "attacker label", age=10, hostname="EVIL-HOST")

    response = await fleet.search(server_ids=[fleet.id("web")])

    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["line"] for e in body["entries"]] == [
        "attacker label",
        f"login password={MARKER} done",
        "older",
    ]
    assert "hunter2" not in response.text
    first = body["entries"][0]
    assert first["hostname"] == fleet.servers["web"][1]  # inventory, not the label
    assert first["server_id"] == fleet.id("web")
    assert (first["source_type"], first["service"], first["level"]) == (
        "application",
        "api",
        "info",
    )
    assert body["entries"][1]["level"] == "error"
    assert body["page"] == {"limit": 200, "next_cursor": None, "has_more": False, "total": None}
    assert body["data_state"] == "ok" and body["source"] == "loki"
    assert body["servers_searched"] == 1 and body["excluded_by_scope_count"] == 0
    assert (
        body["query_stats"]["bytes_scanned"] == 4096 and body["query_stats"]["truncated"] is False
    )


async def test_a_search_across_several_servers_merges_them_by_time(fleet: Fleet) -> None:
    fleet.put("web", "w1", age=50)
    fleet.put("db", "d1", age=40)
    fleet.put("web", "w2", age=30)
    fleet.put("db", "d2", age=20)

    response = await fleet.search(server_ids=[fleet.id("web"), fleet.id("db")])

    assert [e["line"] for e in response.json()["entries"]] == ["d2", "w2", "d1", "w1"]
    assert response.json()["servers_searched"] == 2


async def test_each_filter_narrows_and_none_widens(fleet: Fleet) -> None:
    fleet.put("web", "boom upstream timed out", level="error", service="api")
    fleet.put("web", "boom fine", level="info", service="api")
    fleet.put("web", "boom upstream timed out", level="error", service="worker", age=20)

    def lines(response: httpx.Response) -> list[str]:
        assert response.status_code == 200, response.text
        return [f"{e['service']}:{e['level']}:{e['line']}" for e in response.json()["entries"]]

    base = {"server_ids": [fleet.id("web")]}
    assert len(lines(await fleet.search(**base))) == 3
    assert len(lines(await fleet.search(**base, service="api"))) == 2
    assert len(lines(await fleet.search(**base, level=["error"]))) == 2
    assert len(lines(await fleet.search(**base, level=["error"], service="api"))) == 1
    assert len(lines(await fleet.search(**base, contains="fine"))) == 1
    assert len(lines(await fleet.search(**base, pattern_preset="timeout"))) == 2
    assert len(lines(await fleet.search(**base, contains="boom", pattern_preset="timeout"))) == 2
    assert lines(await fleet.search(**base, contains="nothing matches this")) == []


async def test_an_environment_or_application_selection_resolves_to_servers(fleet: Fleet) -> None:
    fleet.put("web", "in staging")
    fleet.put("prod", "in production", environment="production")
    response = await fleet.search(environment="staging")
    assert [e["line"] for e in response.json()["entries"]] == ["in staging"]
    # At least web and db: the schema is shared, so other tests' staging servers count too.
    assert response.json()["servers_searched"] >= 2

    unknown = await fleet.search(application_id=str(uuid.uuid4()))
    assert unknown.status_code == 200
    assert (unknown.json()["entries"], unknown.json()["servers_searched"]) == ([], 0)


async def test_a_search_writes_no_audit_row(fleet: Fleet) -> None:
    fleet.put("web", "x")
    before = audit_count(fleet.engine)
    assert (await fleet.search(server_ids=[fleet.id("web")])).status_code == 200
    assert audit_count(fleet.engine) == before


# --- the request is structured filters and nothing else (AD-05) ------------------


@pytest.mark.parametrize("field", ["query", "logql", "selector", "regex", "labels"])
async def test_no_field_that_could_carry_logql_is_accepted(fleet: Fleet, field: str) -> None:
    response = await fleet.search(server_ids=[fleet.id("web")], **{field: '{job=~".+"}'})
    assert response.status_code == 422
    assert fleet.fake.requests == []


@pytest.mark.parametrize(
    "bad",
    [
        {"service": 'x"} or {a="b'},
        {"service": "has space"},
        {"level": ["warning"]},
        {"source_type": "journal"},
        {"pattern_preset": ".*"},
        {"environment": "prod"},
        {"limit": 1001},
        {"contains": "a\nb"},
        {"contains": "a" * 257},
        {"server_ids": ["not-a-uuid"]},
    ],
)
async def test_closed_values_are_refused_before_loki_is_asked(
    fleet: Fleet, bad: dict[str, Any]
) -> None:
    response = await fleet.search(**{"server_ids": [fleet.id("web")], **bad})
    assert response.status_code == 422, response.text
    assert fleet.fake.requests == []


async def test_a_naive_or_reversed_range_is_refused(fleet: Fleet) -> None:
    base = {"server_ids": [fleet.id("web")]}
    assert (await fleet.search(**base, **{"from": "2026-01-01T00:00:00"})).status_code == 422
    reversed_ = await fleet.search(**base, **{"from": TO, "to": FROM})
    assert reversed_.status_code == 422
    assert fleet.fake.requests == []


async def test_an_unbounded_query_across_the_estate_is_refused(fleet: Fleet) -> None:
    response = await fleet.search()
    assert response.status_code == 422
    assert fleet.fake.requests == []


async def test_the_range_and_retention_limits_are_structured_422s(fleet: Fleet) -> None:
    base = {"server_ids": [fleet.id("web")]}
    wide = await fleet.search(**base, **{"from": (NOW - timedelta(days=8)).isoformat()})
    assert wide.status_code == 422
    assert wide.json()["error"]["code"] == "query_limit_exceeded"
    assert wide.json()["error"]["details"]["limit"] == "range_seconds"

    old_from = (NOW - timedelta(days=20)).isoformat()
    old_to = (NOW - timedelta(days=19)).isoformat()
    old = await fleet.search(**base, **{"from": old_from, "to": old_to})
    assert old.status_code == 422
    assert old.json()["error"]["details"]["limit"] == "retention_seconds"
    assert old.json()["error"]["details"]["maximum"] == 14 * 86400
    assert fleet.fake.requests == []


async def test_a_hostile_contains_reaches_loki_escaped_and_matches_literally(fleet: Fleet) -> None:
    needle = 'q"uo\\te {x} | ~ `bt` } or {server_id=~".+"} |= "'
    fleet.put("web", "prefix " + needle + " suffix")
    fleet.put("web", "unrelated")

    response = await fleet.search(server_ids=[fleet.id("web")], contains=needle)

    assert [e["line"] for e in response.json()["entries"]] == ["prefix " + needle + " suffix"]
    sent = fleet.loki_log_queries()[0]
    # One selector, then exactly one string literal equal to the needle: it opened nothing.
    prefix = f'{{server_id="{fleet.id("web")}"}} |= '
    assert sent.startswith(prefix)
    assert read_literal(sent[len(prefix) :]) == (needle, "")


# --- scope: a filter, not a check -------------------------------------------------


async def test_an_out_of_scope_server_is_dropped_counted_and_never_queried(fleet: Fleet) -> None:
    fleet.put("web", "visible")
    fleet.put("prod", "secret production line", environment="production")
    narrow(fleet, "VIEWER", STAGING_SCOPE)

    response = await fleet.search(server_ids=[fleet.id("web"), fleet.id("prod")])

    body = response.json()
    assert response.status_code == 200, response.text
    assert [e["line"] for e in body["entries"]] == ["visible"]
    assert (body["servers_searched"], body["excluded_by_scope_count"]) == (1, 1)
    assert fleet.id("prod") not in " ".join(fleet.loki_log_queries())
    assert "secret production line" not in response.text


async def test_selecting_a_whole_environment_the_caller_cannot_read_says_nothing_about_it(
    fleet: Fleet,
) -> None:
    fleet.put("prod", "secret", environment="production")
    narrow(fleet, "VIEWER", STAGING_SCOPE)

    response = await fleet.search(environment="production")

    body = response.json()
    assert response.status_code == 200
    assert (body["entries"], body["servers_searched"], body["excluded_by_scope_count"]) == (
        [],
        0,
        0,
    )
    assert fleet.fake.requests == []  # nothing in scope, so Loki is not asked at all


async def test_a_caller_with_no_log_grant_at_all_is_forbidden(fleet: Fleet) -> None:
    revoke_all(fleet.engine, fleet.caller)
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert fleet.fake.requests == []


async def test_a_viewer_gets_application_lines_only_and_the_query_says_so(fleet: Fleet) -> None:
    fleet.put("web", "app line", source="application")
    fleet.put("web", "nginx line", source="nginx", service="nginx")
    fleet.put("web", "journal line", source="journald", service="sshd.service")
    narrow(fleet, "VIEWER")

    response = await fleet.search(server_ids=[fleet.id("web")])

    assert [e["line"] for e in response.json()["entries"]] == ["app line"]
    assert 'source_type="application"' in fleet.loki_log_queries()[0]


async def test_asking_a_viewer_for_a_system_source_is_a_403_and_is_audited(fleet: Fleet) -> None:
    fleet.put("web", "nginx line", source="nginx", service="nginx")
    narrow(fleet, "VIEWER")

    response = await fleet.search(server_ids=[fleet.id("web")], source_type="nginx")

    assert response.status_code == 403
    assert "logs:read:system" in denials(fleet.engine, fleet.caller)
    assert fleet.fake.requests == []


async def test_a_devops_engineer_reads_system_sources_too(fleet: Fleet) -> None:
    fleet.put("web", "nginx line", source="nginx", service="nginx")
    narrow(fleet, "DEVOPS_ENGINEER")
    response = await fleet.search(server_ids=[fleet.id("web")], source_type="nginx")
    assert [e["line"] for e in response.json()["entries"]] == ["nginx line"]


async def test_system_logs_follow_the_servers_a_caller_holds_them_on(fleet: Fleet) -> None:
    """System sources on staging (DevOps, staging only), application sources everywhere (Viewer)."""
    fleet.put("web", "web app", age=40)
    fleet.put("web", "web nginx", age=30, source="nginx", service="nginx")
    fleet.put("prod", "prod app", age=20, environment="production")
    fleet.put(
        "prod", "prod nginx", age=10, source="nginx", service="nginx", environment="production"
    )
    revoke_all(fleet.engine, fleet.caller)
    grant(fleet.engine, fleet.caller, "DEVOPS_ENGINEER", STAGING_SCOPE)
    grant(fleet.engine, fleet.caller, "VIEWER")  # full scope, application logs only

    response = await fleet.search(server_ids=[fleet.id("web"), fleet.id("prod")])

    assert [e["line"] for e in response.json()["entries"]] == ["prod app", "web nginx", "web app"]
    assert len(fleet.loki_log_queries()) == 2  # one selector per permission group
    assert not any("prod nginx" in e["line"] for e in response.json()["entries"])


async def test_a_line_loki_returns_outside_the_resolved_scope_is_dropped(fleet: Fleet) -> None:
    """Defence in depth: even a selector that somehow matched too much shows nothing."""
    narrow(fleet, "VIEWER", STAGING_SCOPE)

    def foreign(request: httpx.Request) -> httpx.Response:
        stream = {
            "stream": {
                "server_id": fleet.id("prod"),
                "source_type": "application",
                "service": "api",
                "level": "info",
            },
            "values": [[str(NOW_NS - SEC), "leaked production line"]],
        }
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "streams", "result": [stream]}},
        )

    fleet.fake.fail[RANGE] = foreign
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 200
    assert response.json()["entries"] == []
    assert "leaked" not in response.text


# --- paging -----------------------------------------------------------------------


def seed_with_ties(fleet: Fleet) -> list[str]:
    """Eleven lines on two servers; three share one timestamp, two share another."""
    base = NOW_NS - 600 * SEC
    plan = [
        ("web", "a0", 0),
        ("web", "a1", 1),
        ("web", "t1-x", 2),
        ("web", "t1-y", 2),
        ("db", "t1-z", 2),
        ("web", "a3", 3),
        ("db", "t2-x", 4),
        ("db", "t2-y", 4),
        ("web", "a5", 5),
        ("db", "a6", 6),
        ("web", "a7", 7),
    ]
    for server, line, offset in plan:
        fleet.put(server, line, ts=base + offset * SEC)
    return [line for _, line, _ in plan]


async def page_through(fleet: Fleet, **body: Any) -> tuple[list[str], int]:
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        response = await fleet.search(
            server_ids=[fleet.id("web"), fleet.id("db")], cursor=cursor or ..., **body
        )
        assert response.status_code == 200, response.text
        data = response.json()
        seen += [e["line"] for e in data["entries"]]
        pages += 1
        assert pages < 40, "paging did not terminate"
        cursor = data["page"]["next_cursor"]
        assert (cursor is None) == (not data["page"]["has_more"])
        if cursor is None:
            return seen, pages


@pytest.mark.parametrize("flip", [False, True], ids=["ties-as-stored", "ties-flipped"])
@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 10, 11, 12])
@pytest.mark.parametrize("direction", ["backward", "forward"])
async def test_paging_repeats_no_line_and_skips_none_even_inside_a_tie(
    fleet: Fleet, direction: str, limit: int, flip: bool
) -> None:
    fleet.fake.flip_ties = flip
    expected = seed_with_ties(fleet)

    whole = await fleet.search(
        server_ids=[fleet.id("web"), fleet.id("db")], direction=direction, limit=1000
    )
    one_shot = [e["line"] for e in whole.json()["entries"]]
    assert sorted(one_shot) == sorted(expected)

    paged, pages = await page_through(fleet, direction=direction, limit=limit)

    assert paged == one_shot, (direction, limit)
    assert pages == -(-len(expected) // limit)


async def test_a_forward_page_set_is_the_reverse_of_the_backward_one(fleet: Fleet) -> None:
    seed_with_ties(fleet)
    backward, _ = await page_through(fleet, direction="backward", limit=3)
    forward, _ = await page_through(fleet, direction="forward", limit=3)
    assert sorted(backward) == sorted(forward)
    # Time order is exact; ties among equal timestamps may differ by stream order.
    times = {"a0": 0, "a1": 1, "a3": 3, "a5": 5, "a6": 6, "a7": 7}
    assert [times[x] for x in backward if x in times] == sorted(
        (times[x] for x in backward if x in times), reverse=True
    )
    assert [times[x] for x in forward if x in times] == sorted(
        times[x] for x in forward if x in times
    )


async def test_only_the_first_page_carries_the_ingestion_states(fleet: Fleet) -> None:
    seed_with_ties(fleet)
    first = await fleet.search(server_ids=[fleet.id("web"), fleet.id("db")], limit=2)
    second = await fleet.search(
        server_ids=[fleet.id("web"), fleet.id("db")],
        limit=2,
        cursor=first.json()["page"]["next_cursor"],
    )
    assert len(first.json()["ingestion"]) == 2
    assert second.json()["ingestion"] == []
    assert sum(1 for path, _, _ in fleet.fake.requests if path == INSTANT) == 2


async def test_a_tampered_cursor_is_invalid_and_never_a_silent_reset(fleet: Fleet) -> None:
    seed_with_ties(fleet)
    first = await fleet.search(server_ids=[fleet.id("web")], limit=2)
    cursor = first.json()["page"]["next_cursor"]
    response = await fleet.search(server_ids=[fleet.id("web")], limit=2, cursor=cursor[:-3] + "AAA")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    "change",
    [
        {"contains": "a"},
        {"service": "api"},
        {"level": ["info"]},
        {"direction": "forward"},
        {"server_ids": "other"},
    ],
)
async def test_a_cursor_cannot_be_replayed_against_another_filter(
    fleet: Fleet, change: dict[str, Any]
) -> None:
    seed_with_ties(fleet)
    first = await fleet.search(server_ids=[fleet.id("web")], limit=2)
    cursor = first.json()["page"]["next_cursor"]
    body = {"server_ids": [fleet.id("web")], "limit": 2, "cursor": cursor, **change}
    if change.get("server_ids") == "other":
        body["server_ids"] = [fleet.id("web"), fleet.id("db")]
    response = await fleet.search(**body)
    assert response.status_code == 422, response.text


async def test_a_cursor_stops_working_when_the_callers_reach_changes(fleet: Fleet) -> None:
    seed_with_ties(fleet)
    first = await fleet.search(server_ids=[fleet.id("web")], limit=2)
    cursor = first.json()["page"]["next_cursor"]
    narrow(fleet, "VIEWER")  # same servers, fewer source types: a different group
    response = await fleet.search(server_ids=[fleet.id("web")], limit=2, cursor=cursor)
    assert response.status_code == 422


async def test_a_page_whose_tie_order_changed_is_refused_not_repeated(fleet: Fleet) -> None:
    """The tie order is trusted but verified: if it shifted, say so."""
    seed_with_ties(fleet)
    ids = [fleet.id("web"), fleet.id("db")]
    first = await fleet.search(server_ids=ids, limit=3, direction="forward")
    cursor = first.json()["page"]["next_cursor"]
    delivered = first.json()["entries"][-1]["line"]
    assert delivered.startswith("t1-"), "the page must end inside the tie group"
    for line in fleet.fake.lines:
        if line.text == delivered:
            line.text = delivered + " CHANGED"  # no longer what the cursor saw
    response = await fleet.search(server_ids=ids, limit=3, direction="forward", cursor=cursor)
    assert response.status_code == 422


# --- Loki cannot answer: never an empty page ---------------------------------------


async def test_a_refused_connection_is_503_provider_unavailable_and_not_an_empty_page(
    fleet: Fleet,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    fleet.fake.fail[RANGE] = refuse
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert "entries" not in response.text


async def test_a_loki_error_status_is_503(fleet: Fleet) -> None:
    fleet.fake.fail[RANGE] = lambda r: httpx.Response(500, text="internal secret detail")
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 503
    assert "secret detail" not in response.text


async def test_a_slow_loki_is_504_provider_timeout(fleet: Fleet) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    fleet.fake.fail[RANGE] = slow
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"


async def test_log_search_without_a_loki_url_is_not_configured(fleet: Fleet) -> None:
    fleet.monkeypatch.delenv("DCP_LOKI_URL")
    get_settings.cache_clear()
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"
    assert fleet.fake.requests == []


async def test_the_circuit_opens_and_stops_asking_loki(fleet: Fleet) -> None:
    breaker = CircuitBreaker()
    fleet.monkeypatch.setattr(
        logs_service,
        "make_client",
        lambda: LokiClient(
            settings=get_settings(), transport=fleet.fake.transport(), circuit=breaker
        ),
    )
    fleet.fake.fail[RANGE] = lambda r: httpx.Response(500)
    for _ in range(5):
        assert (await fleet.search(server_ids=[fleet.id("web")])).status_code == 503
    before = len(fleet.fake.requests)
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 503
    assert len(fleet.fake.requests) == before


# --- ingestion: a quiet host is not a blind shipper (OPEN-34, issue 93) -------------


async def test_ingestion_states_distinguish_receiving_quiet_and_silent(fleet: Fleet) -> None:
    fleet.put("web", "recent", age=30)
    fleet.put("db", "two hours ago", age=2 * 3600)
    # `prod` has never sent anything.

    response = await fleet.search(environment=..., server_ids=[fleet.id(n) for n in fleet.servers])

    states = {i["hostname"]: i["state"] for i in response.json()["ingestion"]}
    assert states == {
        fleet.servers["web"][1]: "receiving",
        fleet.servers["db"][1]: "quiet",
        fleet.servers["prod"][1]: "silent",
    }


async def test_an_empty_result_from_a_silent_host_says_so(fleet: Fleet) -> None:
    response = await fleet.search(server_ids=[fleet.id("web")], contains="no errors here")
    body = response.json()
    assert body["entries"] == []
    assert [i["state"] for i in body["ingestion"]] == ["silent"]


async def test_when_the_ingestion_probe_fails_the_state_is_unknown_not_silent(fleet: Fleet) -> None:
    fleet.put("web", "line")
    fleet.fake.fail[INSTANT] = lambda r: httpx.Response(500)
    response = await fleet.search(server_ids=[fleet.id("web")])
    assert response.status_code == 200
    assert [e["line"] for e in response.json()["entries"]] == ["line"]
    assert [i["state"] for i in response.json()["ingestion"]] == ["unknown"]


async def test_ingestion_counts_use_a_server_only_selector(fleet: Fleet) -> None:
    fleet.put("web", "line")
    await fleet.search(server_ids=[fleet.id("web")])
    instants = [q for path, q, _ in fleet.fake.requests if path == INSTANT]
    assert len(instants) == 2
    assert all(q.startswith("sum by (server_id) (count_over_time({server_id=") for q in instants)
    assert any("[900s]" in q for q in instants) and any("[86400s]" in q for q in instants)


# --- §19.2 ------------------------------------------------------------------------


async def test_sources_lists_only_what_the_caller_may_read(fleet: Fleet) -> None:
    narrow(fleet, "VIEWER")
    viewer = (await call(fleet.client, "GET", SOURCES)).json()
    narrow(fleet, "DEVOPS_ENGINEER")
    devops = (await call(fleet.client, "GET", SOURCES)).json()

    assert viewer["source_types"] == ["application"]
    assert set(devops["source_types"]) == {
        "application", "journald", "syslog", "nginx", "php_fpm", "docker",
    }  # fmt: skip
    assert devops["levels"] == ["debug", "info", "warn", "error", "critical", "unknown"]
    assert [p["id"] for p in devops["presets"]][:2] == ["http_5xx", "oom_killed"]
    assert devops["limits"] == {
        "max_range_days": 7,
        "retention_days": 14,
        "max_limit": 1000,
        "default_limit": 200,
        "max_contains": 256,
        "max_server_ids": 50,
    }
    assert "expression" not in str(devops)  # the regexes stay server-side


async def test_sources_needs_the_permission_and_takes_no_parameters(fleet: Fleet) -> None:
    revoke_all(fleet.engine, fleet.caller)
    assert (await call(fleet.client, "GET", SOURCES)).status_code == 403
    narrow(fleet, "VIEWER")
    assert (await call(fleet.client, "GET", SOURCES, params={"x": "1"})).status_code == 422


# --- the POST that reads -----------------------------------------------------------


async def test_search_still_works_in_read_only_mode(fleet: Fleet) -> None:
    fleet.put("web", "during an incident")
    enabled = await call(fleet.client, "POST", READ_ONLY_PATH, {"enabled": True, "reason": "test"})
    assert enabled.status_code == 200
    try:
        response = await fleet.search(server_ids=[fleet.id("web")])
        assert response.status_code == 200
    finally:
        await call(fleet.client, "POST", READ_ONLY_PATH, {"enabled": False})


def test_the_published_rate_limit_is_thirty_a_minute() -> None:
    assert (PUBLISHED_LIMIT, PUBLISHED_WINDOW) == (30, 60)  # API_CONTRACTS.md §19.1


async def test_queries_past_the_limit_are_rate_limited(fleet: Fleet) -> None:
    """The limit is lowered to 3 here; the published value is asserted above."""
    key = f"{ratelimit._KEY_PREFIX}log_query:{fleet.caller}"
    await get_redis().delete(key)
    fleet.monkeypatch.setattr(logs_router, "QUERY_RATE_LIMIT", 3)
    try:
        statuses = [
            (await fleet.search(server_ids=[fleet.id("web")])).status_code for _ in range(4)
        ]
    finally:
        await get_redis().delete(key)
    assert statuses == [200, 200, 200, 429]


# --- §16.7, answered by the same engine ----------------------------------------------


async def register_unit(fleet: Fleet, server: str, unit: str) -> None:
    response = await call(
        fleet.client,
        "POST",
        f"/api/v1/servers/{fleet.id(server)}/services",
        {"service_name": unit},
    )
    assert response.status_code == 201, response.text


def unit_url(fleet: Fleet, server: str, unit: str) -> str:
    return f"/api/v1/servers/{fleet.id(server)}/services/{unit}/logs"


async def test_service_logs_are_that_units_journal_lines_only(fleet: Fleet) -> None:
    await register_unit(fleet, "web", "nginx.service")
    fleet.put("web", "unit line", source="journald", service="nginx.service", level="warn")
    fleet.put("web", "other unit", source="journald", service="sshd.service")
    fleet.put("web", "app line", source="application")

    response = await call(fleet.client, "GET", unit_url(fleet, "web", "nginx.service"))

    assert response.status_code == 200, response.text
    assert [e["line"] for e in response.json()["entries"]] == ["unit line"]
    sent = fleet.loki_log_queries()[0]
    assert 'service="nginx.service"' in sent and 'source_type="journald"' in sent


async def test_service_logs_take_a_level_in_the_label_spelling(fleet: Fleet) -> None:
    await register_unit(fleet, "web", "nginx.service")
    fleet.put("web", "w", source="journald", service="nginx.service", level="warn", age=20)
    fleet.put("web", "i", source="journald", service="nginx.service", level="info", age=10)
    url = unit_url(fleet, "web", "nginx.service")
    assert [
        e["line"]
        for e in (await call(fleet.client, "GET", url, params={"level": "warn"})).json()["entries"]
    ] == ["w"]
    assert (await call(fleet.client, "GET", url, params={"level": "warning"})).status_code == 422


async def test_service_logs_need_the_system_permission(fleet: Fleet) -> None:
    await register_unit(fleet, "web", "nginx.service")
    narrow(fleet, "VIEWER")
    response = await call(fleet.client, "GET", unit_url(fleet, "web", "nginx.service"))
    assert response.status_code == 403
    assert fleet.fake.requests == []


async def test_service_logs_say_when_loki_is_down(fleet: Fleet) -> None:
    await register_unit(fleet, "web", "nginx.service")
    fleet.fake.fail[RANGE] = lambda r: httpx.Response(500)
    response = await call(fleet.client, "GET", unit_url(fleet, "web", "nginx.service"))
    assert (response.status_code, response.json()["error"]["code"]) == (503, "provider_unavailable")


async def test_an_instance_unit_cannot_be_selected_by_name_and_says_why(fleet: Fleet) -> None:
    await register_unit(fleet, "web", "getty@tty1.service")
    response = await call(fleet.client, "GET", unit_url(fleet, "web", "getty@tty1.service"))
    assert response.status_code == 422
    assert fleet.fake.requests == []
