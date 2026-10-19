"""Outbound destinations: registered-URL validation and the checked fetch (SECURITY.md §19).

§19 has two halves and both live here.

**Write time -- :func:`validate_registered_url`.** A URL being stored as configuration is
checked against a configured host allowlist. Rules 1, 2, 3 and 7:

* the destination must name a host on the configured allowlist, matched
  **exactly** and case-insensitively (no suffix matching: ``example.com`` must
  not admit ``example.com.attacker.net``, and that is precisely the bug suffix
  matching ships);
* the scheme must be ``https``, or ``http`` for a host explicitly listed as
  insecure-permitted;
* no credentials in the URL, nothing with a fragment or an embedded newline;
* an empty allowlist admits nothing. A deployment that has configured no
  destinations can register none, rather than defaulting to the whole
  internet.

**Request time -- :func:`resolve_checked` and :func:`post_json` (M3.4, ``OPEN-49``).**
Rules 2, 4, 5 and 6. The name is resolved **once**, *every* returned address is checked, and
the connection is made to the address that was checked -- the URL is rewritten to the
literal IP, the ``Host`` header and the TLS ``sni_hostname`` keep the name. There is no
second lookup for a rebind to walk, which is the only form of the check that survives DNS
rebinding. Redirects are not followed; the response body is never read.

An address is refused outright when it is loopback, link-local (which contains the
``169.254.169.254`` metadata endpoint), unspecified, multicast or reserved, *including* when
it arrives IPv4-mapped (``::ffff:169.254.169.254``) or 6to4/NAT64-embedded. A non-global
address (RFC1918, CGNAT, IPv6 ULA, documentation ranges) is refused unless it lies inside
``DCP_SSRF_ALLOWED_NETWORKS``, which is empty by default and cannot name an
always-refused range.

Mandatory rule 9 still stands: nothing here fetches a URL a request supplied.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.core.netpolicy import address_refusal


class UrlNotAllowedError(ValueError):
    """A registered URL failed §19. Raised as a Pydantic validation error.

    The message names the rule, never the value: a rejected URL can carry a
    token in its query string and docs/API_CONTRACTS.md §8 forbids echoing a
    rejected value back.
    """


def validate_registered_url(value: str) -> str:
    """Return ``value`` if it may be stored as an outbound destination."""
    if len(value) > 2048 or any(character in value for character in "\r\n\t "):
        raise UrlNotAllowedError("must be a single-line absolute URL under 2048 characters")

    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise UrlNotAllowedError("scheme must be https")
    if parts.username or parts.password:
        raise UrlNotAllowedError("must not embed credentials")
    if parts.fragment:
        raise UrlNotAllowedError("must not carry a fragment")

    host = (parts.hostname or "").lower()
    if not host:
        raise UrlNotAllowedError("must name a host")

    settings = get_settings()
    allowlist = {entry.lower() for entry in settings.ssrf_host_allowlist}
    if host not in allowlist:
        # Deliberately the same message whether the allowlist is empty or the
        # host is simply absent: both are "not registered", and telling them
        # apart would report whether the deployment has an allowlist at all.
        raise UrlNotAllowedError("host is not on the configured allowlist")
    if parts.scheme == "http" and host not in {
        entry.lower() for entry in settings.ssrf_insecure_hosts
    }:
        raise UrlNotAllowedError("scheme must be https for this host")
    return value


#: Total request budget and connect budget (API_CONTRACTS.md §27: short timeout, high concurrency).
REQUEST_TIMEOUT_SECONDS: Final = 10.0
CONNECT_TIMEOUT_SECONDS: Final = 3.0

#: ``(host, port) -> addresses``. Injectable so a test can stage a rebind.
Resolver = Callable[[str, int], list[str]]


class OutboundError(Exception):
    """An outbound call did not complete; ``reason`` is a closed code, never a URL or words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DestinationRefusedError(OutboundError):
    """A resolved address is one DCP may not connect to (SECURITY.md §19 rule 4)."""

    def __init__(self) -> None:
        super().__init__("destination_refused")


@dataclass(frozen=True, slots=True)
class PostResult:
    status: int
    #: Seconds, from a numeric ``Retry-After`` only; an HTTP-date is ignored.
    retry_after: float | None


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise OutboundError("dns_failed") from None
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def resolve_checked(host: str, port: int, resolver: Resolver | None = None) -> str:
    """Resolve ``host`` once and return the address to connect to.

    **Every** address the name resolves to must pass: a name with one public and one
    private record is refused, because which one a client would use is not under our control
    unless we pick it -- and we then connect to exactly the one returned.
    """
    allowed = tuple(
        ipaddress.ip_network(entry, strict=False) for entry in get_settings().ssrf_allowed_networks
    )
    addresses = (resolver or system_resolver)(host, port)
    if not addresses:
        raise OutboundError("dns_failed")
    if any(address_refusal(address, allowed) for address in addresses):
        raise DestinationRefusedError
    return addresses[0]


def pin_to_checked_address(
    url: str, resolver: Resolver | None = None
) -> tuple[httpx.URL, dict[str, str], dict[str, str]]:
    """``(url with a literal IP, headers, extensions)`` for a registered ``url``.

    Re-runs :func:`validate_registered_url` first (rule 2: validated at configuration time
    *and* at request time). The ``Host`` header and ``sni_hostname`` keep the name, so the
    certificate is verified for the name while the connection goes to the checked address.
    """
    validate_registered_url(url)
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = resolve_checked(host, port, resolver)
    pinned = httpx.URL(url).copy_with(host=address)
    return pinned, {"Host": parts.netloc}, {"sni_hostname": host}


def post_json(
    url: str,
    body: dict[str, Any],
    *,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
) -> PostResult:
    """POST ``body`` as JSON to a registered ``url`` through the checked address.

    Raises :class:`OutboundError` (a closed reason) for a refusal or a transport failure.
    Any HTTP status is returned, not raised: what a status *means* depends on the caller.
    No redirect is followed, no proxy from the environment is used (a proxy would defeat the
    pinning), and the response body is never read.
    """
    pinned, headers, extensions = pin_to_checked_address(url, resolver)
    headers["Content-Type"] = "application/json"
    try:
        with (
            httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "POST",
                pinned,
                content=json.dumps(body, ensure_ascii=True).encode(),
                headers=headers,
                extensions=extensions,
            ) as response,
        ):
            retry_after = response.headers.get("retry-after", "")
            return PostResult(
                response.status_code, float(retry_after) if retry_after.isdigit() else None
            )
    except httpx.TimeoutException:
        raise OutboundError("timeout") from None
    except httpx.HTTPError:
        raise OutboundError("connect_failed") from None
