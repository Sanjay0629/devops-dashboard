"""Secret references, and the one place they are resolved (SECURITY.md §9, ``OPEN-48``).

A notification channel stores a **reference** to its destination secret, never the secret
(``OPEN-13``). The reference is ``env://NAME``; this module turns it into a value, at the
moment of use, in the process that needs it.

``env`` reads ``DCPSECRET_<NAME>`` from the process environment. The prefix is
deliberately **not** ``DCP_``: an unrecognised ``DCP_*`` variable is a startup error by
design (``config._unknown_environment_variables``), so a secret under that prefix would
stop the process booting.

This is a development and small-deployment backend, injected by the platform. It is **not**
a production secret manager and is not presented as one (``OPEN-06`` stays open): the AWS
Secrets Manager and Azure Key Vault backends arrive with the first cloud credential
(M4.3/M4.4) behind the same :func:`resolve`. A resolved value is held by the caller for one
request and never cached here, so a rotation is picked up by the next send.
"""

from __future__ import annotations

import os
import re
from typing import Final, Protocol

from pydantic import SecretStr

ENV_PREFIX: Final = "DCPSECRET_"
_REFERENCE: Final = re.compile(r"^env://([A-Z][A-Z0-9_]{0,63})$")


class SecretUnresolvableError(LookupError):
    """The reference is malformed, names an unknown scheme, or its value is not present.

    The message names the reason, never the reference's target or any value.
    """


class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...


class EnvBackend:
    def get(self, name: str) -> str | None:
        return os.environ.get(f"{ENV_PREFIX}{name}") or None


def validate_reference(reference: str) -> str:
    """Return ``reference`` if it is a well-formed secret reference, else ``ValueError``.

    Used at write time. It checks the shape only; whether the value exists is
    :func:`resolve`'s question and is asked separately.
    """
    if not isinstance(reference, str) or _REFERENCE.fullmatch(reference) is None:
        raise ValueError("must be a secret reference such as env://NAME")
    return reference


def resolve(reference: str, backend: SecretBackend | None = None) -> SecretStr:
    """The value behind ``reference``. Raises :class:`SecretUnresolvableError`."""
    match = _REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
    if match is None:
        raise SecretUnresolvableError("malformed_reference")
    value = (backend or EnvBackend()).get(match.group(1))
    if value is None:
        raise SecretUnresolvableError("not_present")
    return SecretStr(value)
