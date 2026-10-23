"""Fixtures for the deployments unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.core.config import Settings, get_settings
from tests.deployments.harness import TOKEN, Harness, mock_module


@pytest.fixture
def mock() -> Any:
    return mock_module.MockJenkins(user="dcp", token=TOKEN)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in {
        "DCP_JENKINS_URL": "http://mock-jenkins:8080",
        "DCP_JENKINS_USER": "dcp",
        "DCP_JENKINS_TOKEN_REFERENCE": "env://JENKINS_TOKEN",
        "DCPSECRET_JENKINS_TOKEN": TOKEN,
        "DCP_SSRF_HOST_ALLOWLIST": "mock-jenkins",
        "DCP_SSRF_INSECURE_HOSTS": "mock-jenkins",
        "DCP_SSRF_ALLOWED_NETWORKS": "10.0.0.0/8",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def harness(settings: Settings, mock: Any) -> Harness:
    return Harness(settings, mock, httpx.WSGITransport(app=mock))


@pytest.fixture(autouse=True)
def _fresh_breaker() -> Iterator[None]:
    from app.deployments.jenkins import reset_breaker

    reset_breaker()
    yield
    reset_breaker()
