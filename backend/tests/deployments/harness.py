"""Shared by the Jenkins client and status tests: the mock, a harness, a transport that can fail."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from app.core.config import Settings
from app.deployments.jenkins import JenkinsClient
from app.metrics.client import CircuitBreaker

TOKEN = "mock-jenkins-token-for-tests"
JOB = "demo/hello-pipeline"


def _load_mock() -> ModuleType:
    path = Path(__file__).parents[3] / "infra" / "compose" / "mock-jenkins" / "mock_jenkins.py"
    spec = importlib.util.spec_from_file_location("mock_jenkins", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mock_module = _load_mock()


class Harness:
    def __init__(self, settings: Settings, mock: Any, transport: httpx.BaseTransport) -> None:
        self.sleeps: list[float] = []
        self.mock = mock
        self.circuit = CircuitBreaker()
        self.client = JenkinsClient(
            settings,
            transport=transport,
            resolver=lambda host, port: ["10.1.2.3"],
            circuit=self.circuit,
            sleep=self.sleeps.append,
        )

    def requests(self, method: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.mock.requests if method is None or r["method"] == method]


class Flaky(httpx.BaseTransport):
    """Delegates to the mock but lets a test replace what one method answers."""

    def __init__(self, mock: Any, *, post: Any = None, get: Any = None) -> None:
        self._inner = httpx.WSGITransport(app=mock)
        self._post, self._get = post, get
        self.calls: list[tuple[str, str]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        override = self._post if request.method == "POST" else self._get
        if override is not None and not request.url.path.startswith("/crumbIssuer"):
            result = override(request)
            if isinstance(result, Exception):
                raise result
            if result is not None:
                return result
        return self._inner.handle_request(request)


def flaky(settings: Settings, mock: Any, **kwargs: Any) -> tuple[Harness, Flaky]:
    transport = Flaky(mock, **kwargs)
    return Harness(settings, mock, transport), transport
