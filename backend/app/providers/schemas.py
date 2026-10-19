"""Response models for §26.7 (``OPEN-61``, ``OPEN-64``).

``capabilities`` is ``null`` -- not an empty object -- when no adapter serves the account:
unknown is not "supports nothing" (the honest-null rule, CLAUDE.md §5).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.providers.capabilities import Concurrency, Latency, Support

AccountStatus = Literal["ACTIVE", "DISABLED", "ERROR"]
#: ``configured`` means a factory is registered for the provider. It says nothing about
#: credentials or reachability: §26.5 (a real probe) is what says that.
AdapterState = Literal["demonstration", "configured", "not_configured"]


class ActionSupport(BaseModel):
    """One entry per ``ServiceAction``; a model rather than a map so the TS type names them."""

    model_config = ConfigDict(extra="forbid")

    start: Support
    stop: Support
    restart: Support
    status: Support
    logs_tail: Support


class CapabilitiesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_sync: Support
    connectivity_check: Support
    actions: ActionSupport
    async_handle: bool
    concurrency: Concurrency
    latency: Latency


class AccountCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    provider: str
    #: Reported, not enforced here: containing a DISABLED account is the operation gate's job.
    account_status: AccountStatus
    adapter_state: AdapterState
    adapter_id: str | None
    capabilities: CapabilitiesOut | None
    data_state: Literal["ok", "not_configured"]
    #: A closed code, e.g. ``no_adapter_for_provider``.
    reason: str | None
    demo_mode: bool
