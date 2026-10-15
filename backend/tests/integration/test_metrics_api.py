"""§15.4 and §26.1 end to end, through the real authorisation stack (M2.1).

Here rather than in ``tests/metrics/`` because what is being tested is the
*route*: a permission dependency only means something when a real session, a
real grant and the real matcher are in the loop. The catalogue's content is
unit-tested in ``tests/metrics/test_templates.py``; this file is about who
may read it and what it says when nothing is configured.

Both endpoints are reads with no parameters, so there is no CSRF path and no
state-changing branch to cover. What there is:

* **§15.4 requires ``metrics:read``** — a Viewer has it, a caller with no
  grant at all does not, and the refusal must be the permission refusal
  rather than a CSRF rejection (docs/TESTING.md §12 names that trap).
* **§26.1 requires ``admin:integrations``**, which is Administrator-only, so
  the Viewer who may read templates may *not* read the integration page.
* **No endpoint accepts PromQL** (AD-04), and §15.4 accepts no parameter at
  all — including one that looks like a filter.
* **An unconfigured Prometheus reports ``not_configured``**, with no zeros and
  no 5xx.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import Engine

from app.metrics import templates
from tests.integration.inventory_helpers import call, grant, revoke_all, user_id
from tests.integration.test_auth_flow import (  # noqa: F401 — fixtures are used by name
    FakeIdp,
    _login,
    auth_app,
    idp,
    idp_key,
    redis_url,
    subject,
)

TEMPLATES = "/api/v1/metrics/templates"
INTEGRATIONS = "/api/v1/integrations/status"


@pytest.fixture
async def session(
    auth_app: tuple[httpx.AsyncClient, FakeIdp],  # noqa: F811
    migrated: Engine,
) -> tuple[httpx.AsyncClient, Engine, uuid.UUID]:
    """A logged-in caller with no grants at all. Each test grants what it needs."""
    client, provider = auth_app
    await _login(client, provider)
    caller = user_id(migrated, provider.subject)
    revoke_all(migrated, caller)
    return client, migrated, caller


# --- §15.4 -------------------------------------------------------------------


async def test_a_viewer_can_read_the_catalogue(
    session: tuple[httpx.AsyncClient, Engine, uuid.UUID],
) -> None:
    client, engine, caller = session
    grant(engine, caller, "VIEWER")

    response = await call(client, "GET", TEMPLATES)
    assert response.status_code == 200, response.text
    body = response.json()

    assert [entry["template"] for entry in body["templates"]] == list(templates.TEMPLATE_IDS)
    assert body["source"] == "prometheus"
    # Every entry says which OS families it covers, so a client never has to
    # infer that an empty chart meant "unsupported" (OPEN-09).
    for entry in body["templates"]:
        assert entry["applies_to_os"] == ["LINUX"]


async def test_the_catalogue_never_exposes_promql(
    session: tuple[httpx.AsyncClient, Engine, uuid.UUID],
) -> None:
    """AD-04 from the other side: the query strings stay server-side.

    A client that could read the rendered PromQL would be one step from
    sending its own, and the catalogue is the obvious place for it to leak.
    """
    client, engine, caller = session
    grant(engine, caller, "VIEWER")

    body = response_text = (await call(client, "GET", TEMPLATES)).text
    for fragment in ("node_cpu_seconds_total", "rate(", "instance=", "up{", "promql", "query"):
        assert fragment not in body, fragment
    assert "node_" not in response_text


async def test_a_caller_with_no_grant_is_refused_for_the_right_reason(
    session: tuple[httpx.AsyncClient, Engine, uuid.UUID],
) -> None:
    """403 ``forbidden``, and not a CSRF rejection.

    ``app/core/csrf.py`` also answers ``403``, so an assertion on the status
    alone would pass on a CSRF refusal and prove nothing about permissions
    (docs/TESTING.md §12).
    """
    client, _engine, _caller = session
    response = await call(client, "GET", TEMPLATES)
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    assert "csrf" not in body["error"]["message"].lower()


async def test_an_unknown_query_parameter_is_refused(
    session: tuple[httpx.AsyncClient, Engine, uuid.UUID],
) -> None:
    """§15.4 takes no parameters, so every parameter is unknown.

    Answering with the full catalogue as though a filter applied is the §6
    failure: a typo that looks like it worked.
    """
    client, engine, caller = session
    grant(engine, caller, "VIEWER")

    response = await call(client, "GET", TEMPLATES, params={"metrics": "cpu_utilisation"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["details"]["fields"][0]["loc"] == ["query", "metrics"]


# --- §26.1 -------------------------------------------------------------------


async def test_the_integration_page_is_administrator_only(
    session: tuple[httpx.AsyncClient, Engine, uuid.UUID],
) -> None:
    """A Viewer may read metric templates and may not read this.

    Two different permissions on two routes in one router, which is worth
    asserting precisely because they are adjacent.
    """
    client, engine, caller = session
    grant(engine, caller, "VIEWER")
    assert (await call(client, "GET", TEMPLATES)).status_code == 200
    assert (await call(client, "GET", INTEGRATIONS)).status_code == 403

    revoke_all(engine, caller)
    grant(engine, caller, "ADMINISTRATOR")
    assert (await call(client, "GET", INTEGRATIONS)).status_code == 200


async def test_an_unconfigured_prometheus_reports_not_configured(
    session: tuple[httpx.AsyncClient, Engine, uuid.UUID],
) -> None:
    """No ``DCP_PROMETHEUS_URL`` in the test environment, so this is the real path.

    The honest answer is ``not_configured`` — not ``unavailable``, which would
    send someone to debug a host that was never configured, and not a 5xx,
    which would make a status page unusable exactly when it is needed
    (PRD §4.11, CLAUDE.md §7).
    """
    client, engine, caller = session
    grant(engine, caller, "ADMINISTRATOR")

    response = await call(client, "GET", INTEGRATIONS)
    assert response.status_code == 200, response.text
    rows = {row["name"]: row for row in response.json()["integrations"]}

    prometheus = rows["Prometheus"]
    assert prometheus["status"] == "not_configured"
    assert prometheus["type"] == "metrics"
    assert prometheus["last_success_at"] is None
    assert prometheus["circuit_state"] is None

    # Every integration §26.1 names is present, so the page cannot read as
    # "these are the only ones" (the convention app/dashboard/schemas.py uses).
    assert set(rows) == {
        "Identity provider",
        "Prometheus",
        "Loki",
        "Alertmanager",
        "Jenkins",
        "Secret store",
        "Notification channels",
    }
    for name, row in rows.items():
        if name != "Identity provider":
            assert row["status"] == "not_configured", name
        # No error message carries a URL, a credential or a provider payload.
        message = row["last_error_message"] or ""
        assert "://" not in message, name
        assert "password" not in message.lower(), name
