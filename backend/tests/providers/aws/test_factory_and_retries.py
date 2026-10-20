"""The factory's refusals, the setting, and -- against a real HTTP server -- that a write client
cannot retry while a read client does (``OPEN-79``, ``OPEN-82``)."""

from __future__ import annotations

import http.server
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.providers import registry
from app.providers.aws import AwsSsmAdapter, build_adapter
from app.providers.aws.api import Credentials, _build_client, static_credentials_present
from app.providers.errors import NotConfigured
from tests.providers.aws.harness import T0


@pytest.fixture
def version(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DCP_AWS_SSM_DOCUMENT_VERSION", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_without_a_document_version_the_adapter_is_not_configured() -> None:
    with pytest.raises(NotConfigured) as caught:
        build_adapter()
    assert caught.value.detail_code == "document_version_not_set"


def test_with_a_version_the_factory_builds_the_adapter(version: None) -> None:
    adapter = build_adapter()
    assert isinstance(adapter, AwsSsmAdapter)
    assert registry.resolve_adapter("AWS", demo_mode=False).adapter_id == "aws_ssm"


@pytest.mark.parametrize("variable", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
def test_a_static_key_in_the_environment_is_a_refusal_not_a_warning(
    version: None, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "AKIATESTINGNOTAKEY00")
    assert static_credentials_present() is True
    with pytest.raises(NotConfigured) as caught:
        build_adapter()
    assert caught.value.detail_code == "static_credentials_present"
    # And through the registry, which is what the API and the worker call.
    with pytest.raises(NotConfigured):
        registry.resolve_adapter("AWS", demo_mode=False)


def test_an_empty_key_variable_is_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "")
    assert static_credentials_present() is False


@pytest.mark.parametrize("value", ["0", "01", "1.0", "-1", "abc", "1234567", "1 ", "$LATEST"])
def test_the_document_version_is_a_plain_number(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, aws_ssm_document_version=value)  # type: ignore[call-arg]


def test_blank_means_unset() -> None:
    assert Settings(_env_file=None, aws_ssm_document_version="  ").aws_ssm_document_version is None  # type: ignore[call-arg]


def test_credentials_never_print() -> None:
    credentials = Credentials("AKIAEXAMPLE", "very-secret", "token", T0)
    assert "very-secret" not in repr(credentials) and "AKIAEXAMPLE" not in repr(credentials)


# --- a real HTTP server: does the write client retry? --------------------------------------------


class _Server(http.server.ThreadingHTTPServer):
    hits = 0


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.server.hits += 1  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = b'{"__type":"InternalServerError","message":"boom"}'
        self.send_response(500)
        self.send_header("Content-Type", "application/x-amz-json-1.1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    monkeypatch.setattr(time, "sleep", lambda _: None)  # botocore's retry backoff
    server = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _credentials() -> Credentials:
    return Credentials(
        "ASIATESTINGNOTAKEY1", "testing-not-a-secret", "testing-token-0123456789", T0
    )


def _send(client: Any) -> None:
    with pytest.raises(ClientError):
        client.send_command(InstanceIds=["i-0abc123def4567890"], DocumentName="DCP-ServiceControl")


def test_the_write_client_sends_a_failing_command_exactly_once(endpoint: _Server) -> None:
    url = f"http://127.0.0.1:{endpoint.server_address[1]}"
    client = _build_client("ssm", "eu-west-1", _credentials(), True, endpoint_url=url)
    _send(client)
    assert endpoint.hits == 1


def test_the_read_client_does_retry(endpoint: _Server) -> None:
    url = f"http://127.0.0.1:{endpoint.server_address[1]}"
    client = _build_client("ssm", "eu-west-1", _credentials(), False, endpoint_url=url)
    _send(client)
    assert endpoint.hits > 1


def test_no_production_path_passes_an_endpoint_url() -> None:
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3] / "app"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if re.search(r"endpoint_url\s*=\s*[^N\s]", path.read_text(encoding="utf-8"))
        and path.name != "api.py"
    ]
    assert offenders == []
