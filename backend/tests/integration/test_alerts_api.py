"""§20.1 - §20.4 end to end: scope, paging, acknowledgement, assignment and the dashboard.

Alerts are seeded through ``store.fire`` / ``store.resolve`` (the code the webhook itself
runs), so a row here is a row the real pipeline could have produced; the webhook has its own
suite (``test_alert_webhook.py``). The authorisation cases are made by **narrowing a live
caller's grants and watching the answer narrow**, not by reading the handler.

Alert rows persist across tests (the schema is rebuilt once per session), so assertions are
about *this test's* alerts -- membership, ``target_id`` filters, or counts as a before/after
delta -- never about a table total.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.alerts import liveness, store
from app.alerts.catalogue import BY_NAME, SYSTEM_RULES
from app.alerts.lifecycle import dedup_key
from app.core.config import get_settings
from app.core.redaction import MARKER
from app.core.redis import get_redis
from tests.integration.inventory_helpers import (
    STAGING_SCOPE,
    account_id,
    audit_rows,
    call,
    grant,
    register,
    retire_servers,
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

ALERTS = "/api/v1/alerts"
SUMMARY = "/api/v1/dashboard/summary"
HEALTH = "/api/v1/dashboard/health"
INTEGRATIONS = "/api/v1/integrations/status"
READ_ONLY_PATH = "/api/v1/admin/settings/read-only-mode"


@dataclass
class Rig:
    client: httpx.AsyncClient
    engine: Engine
    caller: uuid.UUID
    monkeypatch: pytest.MonkeyPatch
    staging: str
    other_staging: str
    production: str

    # -- seeding ------------------------------------------------------------------------

    def server_environment(self, server: str) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.execute(
                    text("SELECT environment FROM servers WHERE id = :i"), {"i": server}
                ).scalar_one()
            )

    def seed(
        self,
        server: str,
        *,
        rule: str = "HostCpuWarning",
        summary: str = "CPU is high",
        status: str = "FIRING",
        age: int = 0,
        dedup: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, str]:
        """Open (and possibly resolve) an alert about ``server``; returns (id, dedup key)."""
        key = dedup or dedup_key(
            source="PROMETHEUS",
            rule_name=rule,
            target=server,
            discriminators={"n": uuid.uuid4().hex},
        )
        at = now or datetime.now(UTC) - timedelta(seconds=age)
        target = store.ResolvedTarget(
            "server", uuid.UUID(server), uuid.UUID(server), self.server_environment(server)
        )
        with Session(self.engine) as session:
            store.fire(
                session,
                source="PROMETHEUS",
                dedup=key,
                rule_name=rule,
                severity=BY_NAME[rule].severity,
                target=target,
                summary=summary,
                details={"labels": {"server_id": server, "instance": "h:9100"}, "annotations": {}},
                now=at,
            )
            if status == "RESOLVED":
                store.resolve(session, dedup=key, now=at + timedelta(seconds=30))
            session.commit()
            alert_id = session.execute(
                text(
                    "SELECT id FROM alert_events WHERE dedup_key = :k"
                    " ORDER BY first_seen_at DESC LIMIT 1"
                ),
                {"k": key},
            ).scalar_one()
        return str(alert_id), key

    def seed_unlinked(self, *, system: bool = False) -> str:
        rule = SYSTEM_RULES[0].name if system else "HostCpuWarning"
        key = dedup_key(
            source="DCP_SYSTEM" if system else "PROMETHEUS",
            rule_name=rule,
            target=f"unresolved:{uuid.uuid4().hex}",
            discriminators={},
        )
        with Session(self.engine) as session:
            store.fire(
                session,
                source="DCP_SYSTEM" if system else "PROMETHEUS",
                dedup=key,
                rule_name=rule,
                severity="WARNING",
                target=store.UNRESOLVED,
                summary="about nothing in particular",
                details={},
                now=datetime.now(UTC),
            )
            session.commit()
            return str(
                session.execute(
                    text("SELECT id FROM alert_events WHERE dedup_key = :k"), {"k": key}
                ).scalar_one()
            )

    def seed_service_alert(self, server: str, name: str = "nginx.service") -> tuple[str, str]:
        service = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO services (id, server_id, service_name, display_name)"
                    " VALUES (:i, :s, :n, 'nginx')"
                ),
                {"i": service, "s": server, "n": name},
            )
        key = dedup_key(
            source="PROMETHEUS",
            rule_name="SystemdUnitFailed",
            target=server,
            discriminators={"name": f"{name}-{service.hex}"},
        )
        with Session(self.engine) as session:
            store.fire(
                session,
                source="PROMETHEUS",
                dedup=key,
                rule_name="SystemdUnitFailed",
                severity="CRITICAL",
                target=store.ResolvedTarget(
                    "service", service, uuid.UUID(server), self.server_environment(server)
                ),
                summary="nginx failed",
                details={"labels": {"name": name}},
                now=datetime.now(UTC),
            )
            session.commit()
            alert_id = session.execute(
                text("SELECT id FROM alert_events WHERE dedup_key = :k"), {"k": key}
            ).scalar_one()
        return str(alert_id), str(service)

    def resolve_at_source(self, key: str) -> None:
        with Session(self.engine) as session:
            store.resolve(session, dedup=key, now=datetime.now(UTC))
            session.commit()

    def make_user(
        self, role: str, scope: str | None = None, *, status: str = "ACTIVE"
    ) -> uuid.UUID:
        user = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, identity_provider_issuer,"
                    " identity_provider_subject, status) VALUES (:i, :e, :d,"
                    " 'https://idp.invalid', :s, :st)"
                ),
                {
                    "i": user,
                    "e": f"{user}@example.invalid",
                    "d": f"Assignee {user.hex[:6]}",
                    "s": str(user),
                    "st": status,
                },
            )
        if scope is None:
            grant(self.engine, user, role)
        else:
            grant(self.engine, user, role, scope)
        return user

    def narrow(self, role: str, scope: str | None = None) -> None:
        revoke_all(self.engine, self.caller)
        if scope is None:
            grant(self.engine, self.caller, role)
        else:
            grant(self.engine, self.caller, role, scope)

    # -- calling ------------------------------------------------------------------------

    async def list(self, **params: Any) -> httpx.Response:
        return await call(self.client, "GET", ALERTS, params=params or None)

    async def ids(self, **params: Any) -> set[str]:
        response = await self.list(limit=200, **params)
        assert response.status_code == 200, response.text
        return {item["alert_id"] for item in response.json()["items"]}

    async def detail(self, alert_id: str) -> httpx.Response:
        return await call(self.client, "GET", f"{ALERTS}/{alert_id}")

    async def ack(self, alert_id: str, note: str | None = None) -> httpx.Response:
        return await call(self.client, "POST", f"{ALERTS}/{alert_id}/acknowledge", {"note": note})

    async def assign(
        self, alert_id: str, assignee: uuid.UUID | str | None, note: str | None = None
    ) -> httpx.Response:
        body = {"assignee_id": None if assignee is None else str(assignee), "note": note}
        return await call(self.client, "POST", f"{ALERTS}/{alert_id}/assign", body)

    def stored(self, alert_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM alert_events WHERE id = :i"), {"i": alert_id}
                )
                .mappings()
                .one()
            )
            return dict(row)


@pytest.fixture
async def rig(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Rig]:
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    grant(migrated, caller, "ADMINISTRATOR")
    account = account_id(migrated)
    ids = []
    for display, environment in (("A", "staging"), ("B", "staging"), ("C", "production")):
        response = await register(client, account, display_name=display, environment=environment)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    # A cached aggregate would hide the before/after deltas these tests assert.
    monkeypatch.setenv("DCP_DASHBOARD_CACHE_TTL_SECONDS", "0")
    get_settings.cache_clear()
    built = Rig(client, migrated, caller, monkeypatch, ids[0], ids[1], ids[2])
    yield built
    retire_servers(migrated, ids)


def codes(response: httpx.Response) -> list[str]:
    return [field["code"] for field in response.json()["error"]["details"]["fields"]]


# --- §20.1 ---


async def test_the_default_list_is_what_is_still_open(rig: Rig) -> None:
    firing, _ = rig.seed(rig.staging)
    acked, _ = rig.seed(rig.staging)
    assert (await rig.ack(acked)).status_code == 200
    resolved, _ = rig.seed(rig.staging, status="RESOLVED")

    shown = await rig.ids(target_id=rig.staging)

    assert {firing, acked} <= shown and resolved not in shown
    assert resolved in await rig.ids(target_id=rig.staging, status="RESOLVED")
    both = await rig.ids(target_id=rig.staging, status=["FIRING", "RESOLVED"])
    assert {firing, resolved} <= both and acked not in both


async def test_a_list_item_carries_what_the_contract_names(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging, summary="CPU on A is at 85.0%")
    body = (await rig.list(target_id=rig.staging, limit=50)).json()
    item = next(i for i in body["items"] if i["alert_id"] == alert_id)
    assert item["target"] == {"type": "server", "id": rig.staging, "display_name": "A"}
    assert item["links"] == {"server": f"/api/v1/servers/{rig.staging}", "service": None}
    assert (item["status"], item["severity"], item["environment"]) == (
        "FIRING",
        "WARNING",
        "staging",
    )
    assert item["acknowledged_by"] is None and item["resolved_at"] is None
    assert item["occurrence_count"] == 1 and item["rule_name"] == "HostCpuWarning"
    assert body["page"]["total"] is None
    # What this milestone does not produce is stated, so the page cannot imply it does.
    assert body["coverage"]["not_produced_yet"] == [
        {"condition": "application_health_check_failed", "arrives_in": "M5"},
        {"condition": "deployment_failed", "arrives_in": "M5"},
    ]
    assert "cpu" in body["coverage"]["produced"]
    assert "operation_timed_out_or_failed" in body["coverage"]["produced"]  # M4.3, OPEN-89


async def test_filters_severity_environment_target_text_and_time(rig: Rig) -> None:
    warn, _ = rig.seed(rig.staging, rule="HostCpuWarning", summary="disk 100% full")
    crit, _ = rig.seed(rig.staging, rule="HostCpuCritical", summary="disk 1000 full", age=3 * 86400)
    prod, _ = rig.seed(rig.production, rule="HostMemoryCritical", summary="memory")

    assert await rig.ids(target_id=rig.staging, severity="CRITICAL") == {crit}
    assert prod in await rig.ids(environment="production")
    assert warn not in await rig.ids(environment="production")
    assert await rig.ids(target_type="server", target_id=rig.production) == {prod}
    # `%` is a literal in the search text, not a wildcard that would also match "1000".
    assert await rig.ids(target_id=rig.staging, q="100%") == {warn}
    assert await rig.ids(target_id=rig.staging, q="memory") == set()
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert await rig.ids(target_id=rig.staging, **{"from": recent}) == {warn}
    old_only = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    assert await rig.ids(target_id=rig.staging, to=old_only) == {crit}


async def test_unknown_parameters_and_bad_values_are_422_not_a_silent_unfiltered_list(
    rig: Rig,
) -> None:
    typo = await rig.list(severty="CRITICAL")
    assert typo.status_code == 422 and codes(typo) == ["unknown_field"]
    assert (await rig.list(status="OPENISH")).status_code == 422
    assert (await rig.list(limit=0)).status_code == 422
    assert (await rig.list(limit=201)).status_code == 422
    assert (await rig.list(target_type="rack")).status_code == 422
    assert (await rig.list(target_id="nope")).status_code == 422


async def test_paging_neither_repeats_nor_skips_even_inside_a_group_of_equal_times(
    rig: Rig,
) -> None:
    same = datetime.now(UTC) - timedelta(minutes=5)
    seeded = {rig.seed(rig.staging, now=same)[0] for _ in range(7)}
    seeded |= {rig.seed(rig.staging, age=60)[0] for _ in range(2)}

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"target_id": rig.staging, "limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = (await rig.list(**params)).json()
        seen += [item["alert_id"] for item in page["items"]]
        cursor = page["page"]["next_cursor"]
        assert page["page"]["has_more"] is (cursor is not None)
        if cursor is None:
            break
    assert sorted(seen) == sorted(seeded)  # no repeat, no gap
    assert len(seen) == len(set(seen)) == 9


async def test_a_cursor_cannot_be_replayed_against_other_filters_or_tampered_with(
    rig: Rig,
) -> None:
    for _ in range(4):
        rig.seed(rig.staging)
    first = (await rig.list(target_id=rig.staging, limit=2)).json()
    cursor = first["page"]["next_cursor"]
    assert cursor

    other_filter = await rig.list(
        target_id=rig.staging, limit=2, cursor=cursor, severity="CRITICAL"
    )
    assert other_filter.status_code == 422 and codes(other_filter) == ["invalid_cursor"]
    flipped = cursor[:-2] + ("AA" if not cursor.endswith("AA") else "BB")
    tampered = await rig.list(target_id=rig.staging, limit=2, cursor=flipped)
    assert tampered.status_code == 422 and codes(tampered) == ["invalid_cursor"]
    assert (
        await rig.list(target_id=rig.staging, limit=2, cursor="not-a-cursor")
    ).status_code == 422


# --- scope ---


async def test_a_scoped_caller_sees_only_what_their_scope_reaches(rig: Rig) -> None:
    staging, _ = rig.seed(rig.staging)
    production, _ = rig.seed(rig.production)

    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)

    visible = await rig.ids()
    assert staging in visible and production not in visible
    assert (await rig.detail(staging)).status_code == 200
    # The same answer as an alert that does not exist: the response cannot reveal it.
    hidden = await rig.detail(production)
    missing = await rig.detail(str(uuid.uuid4()))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
    assert await rig.ids(target_id=rig.production) == set()  # a filter does not widen scope


async def test_widening_the_scope_widens_the_answer_for_the_same_session(rig: Rig) -> None:
    production, _ = rig.seed(rig.production)
    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)
    assert production not in await rig.ids()
    rig.narrow("DEVOPS_ENGINEER")  # full scope
    assert production in await rig.ids()


async def test_an_alert_about_no_server_is_invisible_to_a_scoped_caller(rig: Rig) -> None:
    unlinked = rig.seed_unlinked()
    system = rig.seed_unlinked(system=True)

    assert {unlinked, system} <= await rig.ids()  # an unconstrained grant sees them

    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)
    visible = await rig.ids()
    assert unlinked not in visible and system not in visible
    assert (await rig.detail(unlinked)).status_code == 404
    assert (await rig.ack(unlinked)).status_code == 404
    assert rig.stored(unlinked)["status"] == "FIRING"


async def test_a_service_alert_is_reached_through_its_server(rig: Rig) -> None:
    staging_alert, service = rig.seed_service_alert(rig.staging)
    production_alert, _ = rig.seed_service_alert(rig.production)

    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)

    visible = await rig.ids()
    assert staging_alert in visible and production_alert not in visible
    body = (await rig.detail(staging_alert)).json()
    assert body["target"] == {"type": "service", "id": service, "display_name": "nginx"}
    assert body["links"] == {
        "server": f"/api/v1/servers/{rig.staging}",
        "service": f"/api/v1/servers/{rig.staging}/services/nginx.service",
    }


async def test_scope_follows_the_servers_current_environment_not_the_stale_copy(
    rig: Rig,
) -> None:
    """``alert_events.environment`` is a snapshot; the server row is the authority."""
    alert_id, _ = rig.seed(rig.staging)
    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)
    assert alert_id in await rig.ids()
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE servers SET environment = 'production' WHERE id = :i"), {"i": rig.staging}
        )
    try:
        assert alert_id not in await rig.ids()
        assert rig.stored(alert_id)["environment"] == "staging"  # the snapshot did not move
    finally:
        with rig.engine.begin() as connection:
            connection.execute(
                text("UPDATE servers SET environment = 'staging' WHERE id = :i"),
                {"i": rig.staging},
            )


async def test_a_viewer_reads_but_cannot_acknowledge_or_assign(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    rig.narrow("VIEWER")

    assert alert_id in await rig.ids()
    assert (await rig.detail(alert_id)).status_code == 200
    denied_ack = await rig.ack(alert_id)
    denied_assign = await rig.assign(alert_id, rig.caller)
    assert denied_ack.status_code == denied_assign.status_code == 403
    assert rig.stored(alert_id)["status"] == "FIRING"
    assert rig.stored(alert_id)["assigned_to"] is None
    with rig.engine.connect() as connection:
        denials = connection.execute(
            text(
                "SELECT count(*) FROM audit_events WHERE action_type = 'AUTHZ_DENIED'"
                " AND actor_id = :a AND sanitized_metadata->>'permission' = 'alert:acknowledge'"
            ),
            {"a": rig.caller},
        ).scalar_one()
    assert denials == 2  # both refusals were audited


# --- §20.3 acknowledge ---


async def test_acknowledging_marks_it_acknowledged_and_never_resolved(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)

    response = await rig.ack(alert_id, note="looking at it, token=abc123def456ghi789")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACKNOWLEDGED"
    assert body["acknowledged_by"]["id"] == str(rig.caller)
    assert body["acknowledged_at"] is not None
    # PRD §4.8: acknowledgement does not mean the problem is gone.
    assert body["resolved_at"] is None
    assert rig.stored(alert_id)["resolved_at"] is None
    rows = [
        r for r in audit_rows(rig.engine, "ALERT_ACKNOWLEDGED") if str(r["target_id"]) == alert_id
    ]
    assert len(rows) == 1
    assert rows[0]["target_type"] == "alert_event" and rows[0]["environment"] == "staging"
    note = rows[0]["sanitized_metadata"]["note"]
    assert "abc123def456ghi789" not in note and MARKER in note


async def test_acknowledging_twice_or_a_closed_alert_is_a_409(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    assert (await rig.ack(alert_id)).status_code == 200
    again = await rig.ack(alert_id)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_state_transition"
    assert again.json()["error"]["details"] == {"current_status": "ACKNOWLEDGED"}

    closed, _ = rig.seed(rig.staging, status="RESOLVED")
    assert (await rig.ack(closed)).status_code == 409
    assert rig.stored(closed)["acknowledged_by"] is None


async def test_two_people_acknowledging_at_once_produce_one_winner(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    first, second = await asyncio.gather(rig.ack(alert_id), rig.ack(alert_id))
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    rows = [
        r for r in audit_rows(rig.engine, "ALERT_ACKNOWLEDGED") if str(r["target_id"]) == alert_id
    ]
    assert len(rows) == 1  # the loser left no audit row


async def test_the_source_still_resolves_an_acknowledged_alert_and_history_is_kept(
    rig: Rig,
) -> None:
    alert_id, key = rig.seed(rig.staging)
    await rig.ack(alert_id)
    with Session(rig.engine) as session:  # a repeat while acknowledged is counted, not reopened
        store.fire(
            session,
            source="PROMETHEUS",
            dedup=key,
            rule_name="HostCpuWarning",
            severity="WARNING",
            target=store.UNRESOLVED,
            summary="again",
            details={},
            now=datetime.now(UTC),
        )
        session.commit()
    mid = (await rig.detail(alert_id)).json()
    assert (mid["status"], mid["occurrence_count"], mid["resolved_at"]) == ("ACKNOWLEDGED", 2, None)

    rig.resolve_at_source(key)

    body = (await rig.detail(alert_id)).json()
    assert body["status"] == "RESOLVED" and body["resolved_at"] is not None
    assert body["acknowledged_by"]["id"] == str(rig.caller)  # who acknowledged it is kept
    assert [h["event"] for h in body["history"]] == ["first_seen", "acknowledged", "resolved"]


async def test_acknowledging_is_blocked_in_read_only_mode(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    enabled = await call(rig.client, "POST", READ_ONLY_PATH, {"enabled": True, "reason": "test"})
    assert enabled.status_code == 200
    try:
        blocked = await rig.ack(alert_id)
        assert blocked.status_code == 423 and blocked.json()["error"]["code"] == "read_only_mode"
        assert (await rig.detail(alert_id)).status_code == 200  # reading still works
    finally:
        await call(rig.client, "POST", READ_ONLY_PATH, {"enabled": False})
    assert rig.stored(alert_id)["status"] == "FIRING"


async def test_unknown_fields_and_long_notes_are_rejected(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    path = f"{ALERTS}/{alert_id}/acknowledge"
    assert (await call(rig.client, "POST", path, {"note": "x" * 501})).status_code == 422
    assert (await call(rig.client, "POST", path, {"status": "RESOLVED"})).status_code == 422
    assert rig.stored(alert_id)["status"] == "FIRING"


# --- §20.4 assign ---


async def test_assigning_to_yourself_to_a_colleague_and_unassigning(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    colleague = rig.make_user("DEVOPS_ENGINEER")

    mine = await rig.assign(alert_id, rig.caller, note="mine")
    assert mine.status_code == 200 and mine.json()["assigned_to"]["id"] == str(rig.caller)

    theirs = await rig.assign(alert_id, colleague)
    assert theirs.json()["assigned_to"]["id"] == str(colleague)
    assert theirs.json()["status"] == "FIRING"  # assignment does not acknowledge

    cleared = await rig.assign(alert_id, None)
    assert cleared.status_code == 200 and cleared.json()["assigned_to"] is None

    events = [h["event"] for h in cleared.json()["history"]]
    assert events == ["first_seen", "assigned", "assigned", "unassigned"]
    assigned = [h for h in cleared.json()["history"] if h["event"] == "assigned"]
    assert assigned[0]["note"] == "mine" and assigned[1]["subject"].startswith("Assignee ")
    rows = [r for r in audit_rows(rig.engine, "ALERT_ASSIGNED") if str(r["target_id"]) == alert_id]
    assert len(rows) == 3


async def test_assignment_is_allowed_on_an_acknowledged_alert(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging)
    await rig.ack(alert_id)
    response = await rig.assign(alert_id, rig.caller)
    assert response.status_code == 200 and response.json()["status"] == "ACKNOWLEDGED"


async def test_assignment_is_refused_on_a_closed_alert(rig: Rig) -> None:
    resolved, _ = rig.seed(rig.staging, status="RESOLVED")
    response = await rig.assign(resolved, rig.caller)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"
    assert rig.stored(resolved)["assigned_to"] is None


async def test_an_assignee_who_could_not_act_on_the_alert_is_refused_without_saying_why(
    rig: Rig,
) -> None:
    production, _ = rig.seed(rig.production)
    viewer = rig.make_user("VIEWER")  # cannot acknowledge
    staging_only = rig.make_user("DEVOPS_ENGINEER", STAGING_SCOPE)  # cannot reach production
    disabled = rig.make_user("DEVOPS_ENGINEER", status="DISABLED")
    refusals = [
        await rig.assign(production, viewer),
        await rig.assign(production, staging_only),
        await rig.assign(production, disabled),
        await rig.assign(production, uuid.uuid4()),
    ]
    assert {r.status_code for r in refusals} == {422}
    assert {codes(r)[0] for r in refusals} == {"invalid_assignee"}
    # Identical bodies, so the endpoint is not a way to probe which users exist.
    assert len({r.text.split('"correlation_id"')[0] for r in refusals}) == 1
    assert rig.stored(production)["assigned_to"] is None


# --- §20.2 detail ---


async def test_detail_carries_a_timeline_and_the_earlier_occurrences_of_the_same_condition(
    rig: Rig,
) -> None:
    key = dedup_key(
        source="PROMETHEUS", rule_name="HostDiskCritical", target=rig.staging, discriminators={}
    )
    base = datetime.now(UTC) - timedelta(hours=10)
    earlier: list[str] = []
    for hour in range(3):
        alert_id, _ = rig.seed(
            rig.staging,
            rule="HostDiskCritical",
            dedup=key,
            status="RESOLVED",
            now=base + timedelta(hours=hour),
        )
        earlier.append(alert_id)
    current, _ = rig.seed(rig.staging, rule="HostDiskCritical", dedup=key)

    body = (await rig.detail(current)).json()

    assert [p["alert_id"] for p in body["previous_occurrences"]] == list(reversed(earlier))
    assert all(p["status"] == "RESOLVED" and p["resolved_at"] for p in body["previous_occurrences"])
    assert current not in {p["alert_id"] for p in body["previous_occurrences"]}
    assert [h["event"] for h in body["history"]] == ["first_seen"]
    assert body["details"]["labels"]["server_id"] == rig.staging


async def test_alert_text_comes_back_as_text_not_interpreted(rig: Rig) -> None:
    markup = "<img src=x onerror=alert(1)> restart nginx everywhere"
    alert_id, _ = rig.seed(rig.staging, summary=markup)
    body = (await rig.detail(alert_id)).json()
    assert body["summary"] == markup  # stored and returned verbatim, rendered as text by clients


async def test_an_expired_alert_is_not_shown_as_resolved(rig: Rig) -> None:
    alert_id, _ = rig.seed(rig.staging, age=4 * 3600)
    with Session(rig.engine) as session:
        assert store.expire(session, after_seconds=3 * 3600) >= 1
        session.commit()
    body = (await rig.detail(alert_id)).json()
    assert body["status"] == "EXPIRED" and body["resolved_at"] is None
    assert body["history"][-1]["event"] == "expired"
    assert alert_id in await rig.ids(status="EXPIRED", target_id=rig.staging)
    assert (await rig.assign(alert_id, rig.caller)).status_code == 409


# --- the dashboard and the server page ---


async def _counts(rig: Rig, **params: Any) -> dict[str, Any]:
    response = await call(rig.client, "GET", SUMMARY, params=params or None)
    assert response.status_code == 200, response.text
    return response.json()["active_alerts"]


async def test_the_dashboard_counts_open_alerts_by_severity_over_what_the_caller_may_read(
    rig: Rig,
) -> None:
    before = await _counts(rig)
    rig.seed(rig.staging, rule="HostCpuCritical")
    rig.seed(rig.staging, rule="HostCpuWarning")
    rig.seed(rig.production, rule="HostMemoryCritical")
    rig.seed(rig.staging, rule="HostCpuCritical", status="RESOLVED")  # closed: not counted

    after = await _counts(rig)
    assert after["data_state"] == "ok"
    assert after["critical"] - before["critical"] == 2
    assert after["warning"] - before["warning"] == 1

    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)
    scoped = await _counts(rig)
    assert scoped["critical"] - before["critical"] <= 1  # production's alert is not counted
    production_only = await _counts(rig, environment="production")
    assert production_only["critical"] == 0  # and a filter cannot reveal it either


async def test_dashboard_filters_apply_to_the_alert_counts(rig: Rig) -> None:
    before_prod = await _counts(rig, environment="production")
    before_staging = await _counts(rig, environment="staging")
    rig.seed(rig.production, rule="HostCpuCritical")
    assert (await _counts(rig, environment="production"))["critical"] == before_prod["critical"] + 1
    assert (await _counts(rig, environment="staging"))["critical"] == before_staging["critical"]


async def test_the_dashboard_row_and_the_server_page_count_this_servers_open_alerts(
    rig: Rig,
) -> None:
    rig.seed(rig.staging, rule="HostCpuCritical")
    rig.seed(rig.staging, rule="HostMemoryCritical")
    rig.seed(rig.staging, rule="HostDiskWarning")
    rig.seed(rig.staging, rule="HostDiskCritical", status="RESOLVED")

    detail = (await call(rig.client, "GET", f"/api/v1/servers/{rig.staging}")).json()
    assert detail["open_alerts"]["data_state"] == "ok"
    assert detail["open_alerts"]["counts"] == {"critical": 2, "warning": 1}

    health = (await call(rig.client, "GET", HEALTH, params={"limit": 200})).json()
    rows = {row["server_id"]: row for row in health["items"]}
    assert rows[rig.staging]["active_alert_count"] == 3
    assert rows[rig.other_staging]["active_alert_count"] == 0  # a real zero, not unknown
    assert health["alerts"]["data_state"] == "ok"


async def test_an_alert_scope_that_does_not_reach_a_server_is_unknown_not_zero(rig: Rig) -> None:
    rig.seed(rig.production, rule="HostCpuCritical")
    rig.narrow("DEVOPS_ENGINEER", STAGING_SCOPE)
    # The server itself is out of scope too (404), so the per-row null is shown on a scope
    # where the server is readable but alerts are not: not constructible with seeded roles,
    # which grant both together. What *is* checkable here is that a scoped list never
    # reports the unreachable server at all.
    health = (await call(rig.client, "GET", HEALTH, params={"limit": 200})).json()
    assert rig.production not in {row["server_id"] for row in health["items"]}
    assert (await call(rig.client, "GET", f"/api/v1/servers/{rig.production}")).status_code == 404


async def test_the_dashboard_says_whether_the_alerting_pipeline_is_confirmed(rig: Rig) -> None:
    rig.monkeypatch.setenv("DCP_ALERTMANAGER_URL", "http://alertmanager.invalid:9093")
    get_settings.cache_clear()
    redis = get_redis()
    await redis.delete(liveness.LAST_WATCHDOG_KEY)
    assert (await _counts(rig))["pipeline"] == "not_confirmed"  # nothing has proved it
    await redis.set(liveness.LAST_WATCHDOG_KEY, datetime.now(UTC).isoformat())
    assert (await _counts(rig))["pipeline"] == "confirmed"
    await redis.set(
        liveness.LAST_WATCHDOG_KEY, (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    )
    assert (await _counts(rig))["pipeline"] == "not_confirmed"


async def test_the_integration_page_has_a_live_alertmanager_row(rig: Rig) -> None:
    async def row() -> dict[str, Any]:
        page = (await call(rig.client, "GET", INTEGRATIONS)).json()
        return next(r for r in page["integrations"] if r["name"] == "Alertmanager")

    assert (await row())["status"] == "not_configured"
    rig.monkeypatch.setenv("DCP_ALERTMANAGER_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    unreachable = await row()
    assert unreachable["status"] == "unavailable"
    assert unreachable["last_error_message"] == "alertmanager_unreachable"
    assert "127.0.0.1" not in str(unreachable)  # a reason code, never the address
