"""Which adapter serves a provider (``OPEN-62``).

Keyed on ``cloud_accounts.provider``. ``demo_mode`` is the only way to get the demonstration
adapter, and ``config.py`` refuses it outside ``local``, so demo and real adapters never mix
in a deployment. Everything else is looked up in ``_FACTORIES``: the AWS adapter (M4.3) is the
only entry; the Azure adapter arrives with M4.4. A provider with no factory --
including every provider this code has never heard of -- is ``NotConfigured`` (fail closed,
CLAUDE.md rule 6). There is no agent adapter (``OPEN-05``): ``ON_PREM`` and ``OTHER`` accounts
have none.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from app.core.config import get_settings
from app.providers.aws import build_adapter as _build_aws
from app.providers.base import ProviderAdapter
from app.providers.demo import DemoAdapter
from app.providers.errors import NotConfigured

_FACTORIES: Final[dict[str, Callable[[], ProviderAdapter]]] = {"AWS": _build_aws}


def registered_providers() -> frozenset[str]:
    """The providers a real adapter exists for. Registration of an account requires one."""
    return frozenset(_FACTORIES)


def resolve_adapter(provider: str, *, demo_mode: bool | None = None) -> ProviderAdapter:
    """The adapter for ``provider``, or ``NotConfigured``. Never returns ``None``."""
    if get_settings().demo_mode if demo_mode is None else demo_mode:
        return DemoAdapter()
    factory = _FACTORIES.get(provider)
    if factory is None:
        raise NotConfigured(detail_code="no_adapter_for_provider")
    return factory()
