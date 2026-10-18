"""The webhook's guards, before anything reaches a database (OPEN-43, SECURITY.md §21).

These run with no PostgreSQL and a Redis nothing listens on: every case here is refused
before the first query, so the point is the *order and the fail-closed defaults*. What
happens to an accepted delivery is ``tests/integration/test_alert_webhook.py``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.alerts.payload import MAX_BODY_BYTES
from app.core.config import get_settings
from tests.alerts.helpers import SECRET, WEBHOOK, alert, bearer, envelope

APP = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests open no socket (a refused connect takes seconds on Windows).

    The rate limiter and the refusal counters are exercised against a real Redis in
    ``tests/integration/test_alert_webhook.py``.
    """

    async def allow(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.alerts.webhook.ratelimit.enforce", allow)
    monkeypatch.setattr("app.alerts.webhook.liveness.note_refused", allow)


def client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str | None = SECRET,
    cidrs: str = "10.0.0.0/8",
    peer: str = "10.1.2.3",
) -> TestClient:
    for name, value in (
        ("DCP_ALERT_WEBHOOK_SECRET", secret),
        ("DCP_ALERT_WEBHOOK_ALLOWED_CIDRS", cidrs),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    from app.main import create_app

    return TestClient(create_app(get_settings()), client=(peer, 50_000))


def post(c: TestClient, body: object = None, *, headers: dict[str, str] | None = None) -> object:
    raw = json.dumps(envelope(alert("HostCpuWarning")) if body is None else body)
    return c.post(
        WEBHOOK, content=raw, headers={"Content-Type": "application/json", **(headers or {})}
    )


def test_no_secret_configured_is_503_never_open(monkeypatch: pytest.MonkeyPatch) -> None:
    response = post(client(monkeypatch, secret=None), headers=bearer())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


def test_an_empty_allowlist_denies_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    response = post(client(monkeypatch, cidrs=""), headers=bearer())
    assert response.status_code == 403


def test_a_peer_outside_the_allowlist_is_403_even_with_the_right_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = post(client(monkeypatch, peer="192.0.2.9"), headers=bearer())
    assert response.status_code == 403


def test_a_forwarded_for_header_cannot_buy_an_allowed_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = client(monkeypatch, peer="192.0.2.9")
    response = post(c, headers={**bearer(), "X-Forwarded-For": "10.1.2.3", "X-Real-IP": "10.1.2.3"})
    assert response.status_code == 403


def test_an_ipv4_mapped_peer_is_matched_as_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.alerts.webhook import _peer_allowed

    assert _peer_allowed("::ffff:10.1.2.3", ("10.0.0.0/8",))
    assert not _peer_allowed("::ffff:192.0.2.9", ("10.0.0.0/8",))
    assert not _peer_allowed(None, ("10.0.0.0/8",))
    assert not _peer_allowed("not-an-ip", ("10.0.0.0/8",))


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-secret-0123456789abcdef0123456789"},
        {"Authorization": SECRET},  # no scheme
        {"Authorization": f"Basic {SECRET}"},
        {"Authorization": f"Bearer {SECRET}x"},
        {"Authorization": f"Bearer {SECRET[:-1]}"},
    ],
)
def test_a_missing_or_wrong_secret_is_401(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    response = post(client(monkeypatch), headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert SECRET not in response.text


def test_the_secret_is_compared_in_constant_time() -> None:
    source = (APP / "alerts/webhook.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert "== secret" not in source and "secret ==" not in source


def test_the_body_is_refused_by_declared_size_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = post(
        client(monkeypatch),
        headers={**bearer(), "Content-Length": str(MAX_BODY_BYTES + 1)},
    )
    assert response.status_code in (400, 413)  # the client library may refuse the lie first


def test_an_oversize_body_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    big = envelope(alert("HostCpuWarning", summary="x" * 900), groupKey="g" * 2000)
    big["alerts"] = [alert("HostCpuWarning", summary="y" * 900) for _ in range(400)]
    assert len(json.dumps(big)) > MAX_BODY_BYTES
    response = post(client(monkeypatch), big, headers=bearer())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "[]",
        {"version": "4"},
        envelope(version="3"),
        envelope(unexpected="field"),
        envelope(status="bogus"),
    ],
)
def test_an_invalid_body_is_422_and_never_echoes_the_input(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    c = client(monkeypatch)
    raw = body if isinstance(body, str) else json.dumps(body)
    response = c.post(
        WEBHOOK, content=raw, headers={"Content-Type": "application/json", **bearer()}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert "bogus" not in response.text  # a rejected value is not echoed (API_CONTRACTS §8)


def test_refusal_reasons_are_a_closed_set() -> None:
    from app.alerts import liveness

    assert liveness.REFUSAL_REASONS == (
        "not_configured",
        "source_address",
        "rate_limited",
        "bad_secret",
        "body_too_large",
        "invalid_body",
    )


# --- the exemption and the "no operation" pins -------------------------------------


def test_the_csrf_exemption_is_exactly_the_webhook_path() -> None:
    from app.main import create_app

    app = create_app(get_settings())
    csrf = next(m for m in app.user_middleware if m.cls.__name__ == "CsrfMiddleware")
    assert csrf.kwargs["exempt_paths"] == frozenset({"/api/v1/webhooks/alerts"})


def test_another_post_without_a_csrf_token_is_still_refused() -> None:
    from app.main import create_app

    c = TestClient(create_app(get_settings()))
    response = c.post("/api/v1/logs/query", json={})
    assert response.status_code == 403


#: Modules whose import would let alert handling run a command, call out or enqueue work.
FORBIDDEN = (
    "subprocess",
    "os",
    "shlex",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "celery",
    "asyncio.subprocess",
    "app.workers",
    "app.services",
    "app.metrics.client",
    "app.logs.client",
)


@pytest.mark.parametrize("module", ["ingest", "store", "webhook", "payload", "lifecycle"])
def test_alert_handling_imports_nothing_that_can_execute_call_out_or_enqueue(module: str) -> None:
    """SECURITY.md §21: an alert payload can never cause an operation to execute."""
    tree = ast.parse((APP / f"alerts/{module}.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    bad = {
        name
        for name in imported
        for banned in FORBIDDEN
        if name == banned or name.startswith(banned + ".")
    }
    assert not bad, f"app/alerts/{module}.py imports {sorted(bad)}"
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"eval", "exec", "compile", "__import__", "open"}
