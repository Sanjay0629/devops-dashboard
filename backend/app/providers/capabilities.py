"""Capability sets (``OPEN-61``): what an adapter can do, in three values rather than two.

``unsupported`` is "the provider cannot" and is shown as "not supported by this provider"
(PRD §4.11). ``not_configured`` is "we have not set it up". Merging them is the confusion the
``data_state`` vocabulary exists to prevent, so a boolean is not offered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.providers.errors import NotConfigured, UnsupportedOperation
from app.providers.types import ServiceAction


class Support(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    NOT_CONFIGURED = "not_configured"


class Concurrency(StrEnum):
    NONE = "none"
    PER_TARGET_SERIALISED = "per_target_serialised"
    ACCOUNT_THROTTLED = "account_throttled"
    ADAPTER_DEFINED = "adapter_defined"


class Latency(StrEnum):
    IMMEDIATE = "immediate"
    SECONDS = "seconds"
    TENS_OF_SECONDS = "tens_of_seconds"


@dataclass(frozen=True)
class CapabilitySet:
    inventory_sync: Support
    connectivity_check: Support
    actions: Mapping[ServiceAction, Support]
    async_handle: bool
    concurrency: Concurrency
    latency: Latency

    def __post_init__(self) -> None:
        if set(self.actions) != set(ServiceAction):
            raise ValueError("actions must name every ServiceAction exactly once")
        # A frozen dataclass holding a plain dict is still mutable through the dict.
        object.__setattr__(self, "actions", MappingProxyType(dict(self.actions)))


def ensure_supported(
    capabilities: CapabilitySet, action: ServiceAction, *, adapter_id: str
) -> None:
    """Raise unless ``action`` is ``supported``. Unknown or missing is a refusal, never a pass."""
    support = capabilities.actions.get(action)
    detail = f"action_{action.value}"
    if support is Support.SUPPORTED:
        return
    if support is Support.UNSUPPORTED:
        raise UnsupportedOperation(adapter_id=adapter_id, detail_code=detail)
    raise NotConfigured(adapter_id=adapter_id, detail_code=detail)
