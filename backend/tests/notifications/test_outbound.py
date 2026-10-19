"""SECURITY.md §19 request-time half, and the secret reference (``OPEN-48``, ``OPEN-49``)."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from app.core import secrets
from app.core.config import ConfigurationError, get_settings, load_settings
from app.core.netpolicy import address_refusal, network_problem
from app.core.ssrf import (
    DestinationRefusedError,
    OutboundError,
    post_json,
    resolve_checked,
)

PUBLIC = "93.184.216.34"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.5.5.5",
        "169.254.169.254",
        "::ffff:169.254.169.254",  # the metadata endpoint, IPv4-mapped
        "::ffff:127.0.0.1",
        "::1",
        "fe80::1",
        "0.0.0.0",  # noqa: S104 -- the address under test, not a bind
        "::",
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "2002:a9fe:a9fe::1",  # 6to4 wrapping 169.254.169.254
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "10.0.0.5",  # private, no allowlist
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",  # CGNAT
        "fd00::1",  # ULA
        "not-an-address",
        "",
    ],
)
def test_addresses_that_are_refused(address: str) -> None:
    assert address_refusal(address) == "destination_refused"


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_permitted(address: str) -> None:
    assert address_refusal(address) is None


def test_a_private_range_needs_an_allowlist_and_the_allowlist_cannot_reach_never_space() -> None:
    allowed = (ipaddress.ip_network("172.16.0.0/12"),)
    assert address_refusal("172.18.0.4", allowed) is None
    assert address_refusal("10.0.0.5", allowed) == "destination_refused"
    # Allowing a range never makes loopback/metadata reachable, even if a table were wrong.
    assert address_refusal("127.0.0.1", (ipaddress.ip_network("127.0.0.0/8"),))
    assert address_refusal("169.254.169.254", (ipaddress.ip_network("169.254.0.0/16"),))
    # An IPv4-mapped spelling of an allowed private address is judged as the IPv4 address.
    assert address_refusal("::ffff:172.18.0.4", allowed) is None


@pytest.mark.parametrize(
    "network",
    ["0.0.0.0/0", "::/0", "127.0.0.0/8", "169.254.0.0/16", "169.254.169.254/32", "0.0.0.0/4"],
)
def test_networks_that_may_not_be_allowlisted(network: str) -> None:
    assert network_problem(ipaddress.ip_network(network)) is not None


def test_the_allowlist_setting_refuses_them_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_SSRF_ALLOWED_NETWORKS", "172.16.0.0/12, 192.168.0.0/16")
    assert load_settings().ssrf_allowed_networks == ("172.16.0.0/12", "192.168.0.0/16")
    for bad in ("127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/0", "nonsense"):
        monkeypatch.setenv("DCP_SSRF_ALLOWED_NETWORKS", f"10.0.0.0/8,{bad}")
        with pytest.raises(ConfigurationError):
            load_settings()


def _allow(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "hooks.example.test")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def test_every_resolved_address_must_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """One public and one private record is refused: we do not get to pick which is used."""
    _allow(monkeypatch)
    with pytest.raises(DestinationRefusedError):
        resolve_checked("hooks.example.test", 443, lambda h, p: [PUBLIC, "10.0.0.5"])
    assert resolve_checked("hooks.example.test", 443, lambda h, p: [PUBLIC]) == PUBLIC


def test_an_empty_resolution_is_a_transient_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch)
    with pytest.raises(OutboundError) as caught:
        resolve_checked("hooks.example.test", 443, lambda h, p: [])
    assert caught.value.reason == "dns_failed"


def test_post_connects_to_the_checked_address_and_keeps_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rebinding defence: one lookup, and the connection goes to what was checked."""
    _allow(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"retry-after": "7"})

    lookups: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        lookups.append(host)
        # A rebind would answer differently the second time; there is no second time.
        return [PUBLIC] if len(lookups) == 1 else ["169.254.169.254"]

    result = post_json(
        "https://hooks.example.test/services/T/B/x",
        {"a": 1},
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )
    assert result.status == 200 and result.retry_after == 7.0
    assert len(lookups) == 1
    (request,) = seen
    assert request.url.host == PUBLIC
    assert request.headers["host"] == "hooks.example.test"
    assert request.extensions["sni_hostname"] == "hooks.example.test"
    assert request.url.path == "/services/T/B/x"


def test_a_redirect_is_returned_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest"})

    result = post_json(
        "https://hooks.example.test/x",
        {},
        resolver=lambda h, p: [PUBLIC],
        transport=httpx.MockTransport(handler),
    )
    assert result.status == 302 and len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.test/x",  # not on the allowlist
        "https://hooks.example.test.attacker.net/x",  # suffix trick
        "http://hooks.example.test/x",  # plain http for a non-insecure host
        "https://user:pw@hooks.example.test/x",  # credentials in the URL
        "ftp://hooks.example.test/x",
        "https://hooks.example.test/x#frag",
    ],
)
def test_a_url_that_fails_the_request_time_validation_is_never_sent(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _allow(monkeypatch)
    sent: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with pytest.raises(ValueError):
        post_json(url, {}, resolver=lambda h, p: [PUBLIC], transport=httpx.MockTransport(handler))
    assert not sent


def test_transport_failures_have_closed_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom: secret-token-in-message")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for handler, reason in ((refuse, "connect_failed"), (slow, "timeout")):
        with pytest.raises(OutboundError) as caught:
            post_json(
                "https://hooks.example.test/x",
                {},
                resolver=lambda h, p: [PUBLIC],
                transport=httpx.MockTransport(handler),
            )
        assert caught.value.reason == reason
        assert "secret-token" not in str(caught.value)


# --- secret references ---------------------------------------------------------------------


class DictBackend:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str | None:
        return self.values.get(name)


@pytest.mark.parametrize("reference", ["env://SLACK_OPS", "env://A", "env://A1_B2"])
def test_well_formed_references(reference: str) -> None:
    assert secrets.validate_reference(reference) == reference


@pytest.mark.parametrize(
    "reference",
    [
        "https://hooks.slack.com/services/T/B/x",  # a URL is the credential itself
        "env://lower",
        "env://",
        "env://A B",
        "env://" + "A" * 65,
        "vault://x",
        "SLACK_OPS",
        "env://A\n",
    ],
)
def test_malformed_references(reference: str) -> None:
    with pytest.raises(ValueError):
        secrets.validate_reference(reference)


def test_resolution_uses_the_backend_and_names_no_value() -> None:
    backend = DictBackend({"OPS": "https://hooks.example.test/canary-secret"})
    assert secrets.resolve("env://OPS", backend).get_secret_value().endswith("canary-secret")
    assert "canary-secret" not in repr(secrets.resolve("env://OPS", backend))
    with pytest.raises(secrets.SecretUnresolvableError) as caught:
        secrets.resolve("env://MISSING", backend)
    assert str(caught.value) == "not_present"
    with pytest.raises(secrets.SecretUnresolvableError):
        secrets.resolve("https://x", backend)


def test_the_env_backend_reads_the_non_dcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DCP_*`` would be a startup error (extra="forbid"); ``DCPSECRET_*`` is not."""
    monkeypatch.setenv("DCPSECRET_OPS", "value-1")
    assert secrets.resolve("env://OPS").get_secret_value() == "value-1"
    load_settings()  # the secret variable does not trip the unknown-DCP_-variable guard
    monkeypatch.setenv("DCPSECRET_OPS", "")
    with pytest.raises(secrets.SecretUnresolvableError):
        secrets.resolve("env://OPS")
