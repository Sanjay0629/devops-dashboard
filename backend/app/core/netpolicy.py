"""Which addresses an outbound connection may reach (SECURITY.md §19 rule 4).

Pure and free of settings, so ``core/config.py`` can validate ``DCP_SSRF_ALLOWED_NETWORKS``
with the same table ``core/ssrf.py`` enforces -- one definition of "never".
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Final

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Never reachable, and never allowable by configuration. Loopback, link-local (the cloud
#: metadata endpoint is in 169.254.0.0/16), "this network", multicast, reserved, and the
#: IPv6 prefixes that *embed* an IPv4 address (judged by the embedded address instead).
ALWAYS_REFUSED: Final[tuple[IPNetwork, ...]] = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
        "::ffff:0:0/96",
        "2002::/16",
        "64:ff9b::/96",
    )
)

_SIX_TO_FOUR: Final = ipaddress.ip_network("2002::/16")
_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")


def embedded_ipv4(address: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address carries (mapped, 6to4 or NAT64), else ``None``."""
    if isinstance(address, ipaddress.IPv4Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address in _SIX_TO_FOUR:
        return ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def address_refusal(address: str, allowed_networks: Iterable[IPNetwork] = ()) -> str | None:
    """``None`` if a connection to ``address`` is permitted, else a closed reason code.

    A string that is not an IP literal is refused: this function judges *resolved*
    addresses. An IPv6 address that wraps an IPv4 one is judged as the IPv4 address.
    """
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return "destination_refused"
    judged = embedded_ipv4(parsed) or parsed
    if (
        judged.is_loopback
        or judged.is_link_local
        or judged.is_unspecified
        or judged.is_multicast
        or judged.is_reserved
        or any(judged.version == n.version and judged in n for n in ALWAYS_REFUSED)
    ):
        return "destination_refused"
    if judged.is_global:
        return None
    if any(judged.version == n.version and judged in n for n in allowed_networks):
        return None
    return "destination_refused"


def network_problem(network: IPNetwork) -> str | None:
    """Why ``network`` may not appear in an allowlist, or ``None``."""
    if network.prefixlen == 0:
        return "an all-addresses network is not a restriction"
    for refused in ALWAYS_REFUSED:
        if network.version == refused.version and network.overlaps(refused):
            return "must not overlap loopback, link-local, multicast or reserved space"
    return None
