"""Write-time validation of registered outbound destinations (docs/SECURITY.md §19).

§19 has two halves and this module is the **first** one: a URL being stored as
configuration is checked here, at write time, against a configured host
allowlist. The second half — resolving DNS at request time and checking the
address actually connected to against the denied ranges, so a rebind cannot
walk the check — belongs with the code that performs the fetch, and the first
of those arrives with the health-check runner (M4/M5). Nothing in this codebase
fetches any of these URLs yet; storing one is all that happens today, and
mandatory rule 9 still stands.

So the rules enforced here are §19's 1, 2, 3 and 7:

* the destination must name a host on the configured allowlist, matched
  **exactly** and case-insensitively (no suffix matching: ``example.com`` must
  not admit ``example.com.attacker.net``, and that is precisely the bug suffix
  matching ships);
* the scheme must be ``https``, or ``http`` for a host explicitly listed as
  insecure-permitted;
* no credentials in the URL, no port other than the scheme's default or an
  explicit one, nothing with a fragment or an embedded newline;
* an empty allowlist admits nothing. A deployment that has configured no
  destinations can register none, rather than defaulting to the whole
  internet.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import get_settings


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
