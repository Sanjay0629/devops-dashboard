"""Scope applied inside the query, and the SQL agreeing with the matcher.

Two things, and the second is the important one.

**One:** a narrowly scoped caller sees only their rows, and the rows they
cannot see are not merely hidden from the page — they are never selected, so
page sizes and ``has_more`` cannot be used to count them
(docs/SECURITY.md §20). ``page.total`` is ``null`` on a cursor page by §5, so
the assertion is on the items, which is the stronger statement anyway.

**Two:** docs/SECURITY.md §4 rule 7 says exactly one function decides whether
a caller may act, and "duplicated authorisation logic is a defect". Filtering
inside SQL cannot call ``permits()`` per row, so ``app/authz/queries.py``
translates the same grants into a ``WHERE`` clause — which is a second
implementation unless something holds the two together. This file is that
something: a grid of scopes against a grid of real rows, asserting that the
set SQL returns is *exactly* the set the matcher allows. A rule changed in one
and not the other fails here.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.authz.matcher import Grant, Target, permits
from app.authz.queries import readable_servers
from app.inventory.models import Server
from tests.integration.inventory_helpers import (
    SERVERS,
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

ACCOUNT_A = uuid.UUID("44444444-4444-4444-4444-444444444444")
ACCOUNT_B = uuid.UUID("55555555-5555-5555-5555-555555555555")
APP_A = "66666666-6666-6666-6666-666666666666"


def _row(environment: str, account: uuid.UUID) -> Server:
    """An unsaved server, for the cross-check's in-memory half."""
    return Server(
        id=uuid.uuid4(),
        cloud_account_id=account,
        provider_resource_id=f"res-{uuid.uuid4()}",
        hostname=f"h{uuid.uuid4().hex[:8]}.internal.example",
        display_name="Cross Check",
        operating_system="LINUX",
        environment=environment,
        management_method="RESTRICTED_AGENT",
        monitoring_enabled=True,
        agent_status="UNKNOWN",
        status="PENDING",
    )


#: Scopes spanning every shape the matcher distinguishes.
SCOPES: list[dict[str, Any]] = [
    {"environment": ["staging"]},
    {"environment": ["production"]},
    {"environment": ["*"]},
    {"environment": ["staging", "production"]},
    {"cloud_account": [str(ACCOUNT_A)]},
    {"cloud_account": ["*"]},
    {"environment": ["staging"], "cloud_account": [str(ACCOUNT_B)]},
    {"server": ["*"]},
    {"application": [APP_A]},
    {"application": ["*"]},
    {
        "environment": ["development", "staging", "production", "other"],
        "cloud_account": ["*"],
        "server": ["*"],
        "application": ["*"],
    },
    {},
    {"unknown_dimension": ["x"]},
    {"environment": "staging"},
]


@pytest.mark.parametrize("scope", SCOPES, ids=[str(scope) for scope in SCOPES])
@pytest.mark.parametrize("effect", ["ALLOW", "DENY"])
def test_the_sql_filter_selects_exactly_what_the_matcher_allows(
    migrated: Engine, database_url: str, scope: dict[str, Any], effect: str
) -> None:
    """Rule 7, enforced: one decision, two evaluation strategies, same answer.

    The ``DENY`` half matters as much as the ``ALLOW`` half — a deny whose
    scope is unreadable or empty applies to *everything* in the matcher, and a
    translation that quietly made it apply to nothing would be a silent
    widening with no other test to catch it.
    """
    rows = [
        _row("staging", ACCOUNT_A),
        _row("production", ACCOUNT_A),
        _row("development", ACCOUNT_B),
        _row("staging", ACCOUNT_B),
        _row("other", ACCOUNT_A),
    ]
    # An ALLOW alongside the DENY, so the deny has something to subtract from;
    # a deny on its own leaves nothing to compare.
    grants = (
        [Grant("server", "read", effect, scope)]
        if effect == "ALLOW"
        else [
            Grant("server", "read", "ALLOW", {"environment": ["*"]}),
            Grant("server", "read", "DENY", scope),
        ]
    )

    expected = {
        row.id
        for row in rows
        if permits(
            grants,
            "server",
            "read",
            Target(
                environment=row.environment,
                cloud_account=str(row.cloud_account_id),
                server=str(row.id),
            ),
        ).allowed
    }

    engine = create_engine(database_url, future=True, connect_args={"connect_timeout": 10})
    try:
        with Session(engine, future=True) as session:
            session.execute(
                text(
                    "INSERT INTO cloud_accounts (id, provider, account_reference, display_name,"
                    " region_configuration, status, created_at, updated_at)"
                    " VALUES (:a, 'OTHER', 'xcheck-a', 'Cross Check A', '{}'::jsonb, 'ACTIVE',"
                    " now(), now()), (:b, 'OTHER', 'xcheck-b', 'Cross Check B', '{}'::jsonb,"
                    " 'ACTIVE', now(), now()) ON CONFLICT (id) DO NOTHING"
                ),
                {"a": ACCOUNT_A, "b": ACCOUNT_B},
            )
            session.add_all(rows)
            session.flush()
            selected = set(
                session.execute(
                    select(Server.id).where(
                        Server.id.in_([row.id for row in rows]),
                        Server.deleted_at.is_(None),
                        readable_servers(grants, "server", "read"),
                    )
                )
                .scalars()
                .all()
            )
            # Never committed: this is a comparison, not a fixture.
            session.rollback()
    finally:
        engine.dispose()

    assert selected == expected, f"scope={scope} effect={effect}"


async def test_a_narrowly_scoped_caller_does_not_see_other_environments(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """The M1.5 security check: query as a narrow user, confirm the rest is gone.

    The hidden rows are registered by the same caller while they are still
    full-scope, so there is no doubt the rows exist — then the grant is
    narrowed and the same list is read again.
    """
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    account = account_id(migrated)
    marker = uuid.uuid4().hex[:8]

    grant(migrated, caller, "ADMINISTRATOR")
    created: dict[str, str] = {}
    for environment in ("staging", "production", "development"):
        response = await register(
            client, account, environment=environment, display_name=f"{marker}-{environment}"
        )
        assert response.status_code == 201, response.text
        created[environment] = response.json()["id"]

    everything = await call(client, "GET", SERVERS, params={"q": marker})
    assert {item["id"] for item in everything.json()["items"]} == set(created.values())

    revoke_all(migrated, caller)
    grant(migrated, caller, "VIEWER", scope=STAGING_SCOPE)

    narrowed = await call(client, "GET", SERVERS, params={"q": marker})
    assert narrowed.status_code == 200, narrowed.text
    items = narrowed.json()["items"]
    assert [item["id"] for item in items] == [created["staging"]]
    # Not merely absent from the page: the collection reports no more to come,
    # so a client cannot infer the hidden rows from has_more either.
    assert narrowed.json()["page"]["has_more"] is False
    assert narrowed.json()["page"]["total"] is None

    # And the same rows are 404 by id, not 403 (§2): a 403 would confirm they
    # exist, which is exactly what the scope is hiding.
    for environment in ("production", "development"):
        response = await call(client, "GET", f"{SERVERS}/{created[environment]}")
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"

    # The one in scope still reads.
    assert (await call(client, "GET", f"{SERVERS}/{created['staging']}")).status_code == 200


async def test_a_filter_cannot_widen_scope(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> None:
    """§6: "filters never widen scope — they intersect with it"."""
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    account = account_id(migrated)
    marker = uuid.uuid4().hex[:8]

    grant(migrated, caller, "ADMINISTRATOR")
    response = await register(
        client, account, environment="production", display_name=f"{marker}-prod"
    )
    assert response.status_code == 201, response.text

    revoke_all(migrated, caller)
    grant(migrated, caller, "VIEWER", scope=STAGING_SCOPE)

    asked = await call(client, "GET", SERVERS, params={"q": marker, "environment": "production"})
    assert asked.status_code == 200, asked.text
    assert asked.json()["items"] == []


APP_B = "77777777-7777-7777-7777-777777777777"


@pytest.mark.parametrize("scope", SCOPES, ids=[str(scope) for scope in SCOPES])
@pytest.mark.parametrize("effect", ["ALLOW", "DENY"])
def test_the_sql_filter_for_deployment_targets_selects_exactly_what_the_matcher_allows(
    migrated: Engine, database_url: str, scope: dict[str, Any], effect: str
) -> None:
    """The same rule-7 cross-check for a row kind with an environment *and* an application.

    A target has no account and no server, so a grant naming one reaches none (A-09); it does
    have an application, so one naming an application reaches that application's targets only.
    """
    from app.authz.queries import readable_deployment_targets
    from app.deployments.models import DeploymentTarget

    pairs = [
        ("staging", APP_A),
        ("production", APP_A),
        ("development", APP_B),
        ("staging", APP_B),
        ("other", APP_A),
    ]
    grants = (
        [Grant("deployment", "read", effect, scope)]
        if effect == "ALLOW"
        else [
            Grant("deployment", "read", "ALLOW", {"environment": ["*"]}),
            Grant("deployment", "read", "DENY", scope),
        ]
    )
    rows = [
        DeploymentTarget(
            id=uuid.uuid4(),
            application_id=uuid.UUID(application),
            environment=environment,
            pipeline_reference="team/xcheck",
            parameter_template={},
            allowed_ref_patterns=[],
            health_check_config={},
            rollback_supported=False,
            status="ACTIVE",
        )
        for environment, application in pairs
    ]
    expected = {
        row.id
        for row in rows
        if permits(
            grants,
            "deployment",
            "read",
            Target(environment=row.environment, application=str(row.application_id)),
        ).allowed
    }

    engine = create_engine(database_url, future=True, connect_args={"connect_timeout": 10})
    try:
        with Session(engine, future=True) as session:
            for application in (APP_A, APP_B):
                session.execute(
                    text(
                        "INSERT INTO applications (id, name, slug) VALUES (:i, :n, :s)"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "i": uuid.UUID(application),
                        "n": f"xcheck {application}",
                        "s": f"x{application[:8]}",
                    },
                )
            session.add_all(rows)
            session.flush()
            selected = set(
                session.execute(
                    select(DeploymentTarget.id).where(
                        DeploymentTarget.id.in_([row.id for row in rows]),
                        readable_deployment_targets(grants, "deployment", "read"),
                    )
                )
                .scalars()
                .all()
            )
            session.rollback()  # a comparison, not a fixture
    finally:
        engine.dispose()

    assert selected == expected, f"scope={scope} effect={effect}"
