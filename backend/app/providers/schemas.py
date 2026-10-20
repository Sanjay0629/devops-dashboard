"""Response models for §26.7 (``OPEN-61``, ``OPEN-64``).

``capabilities`` is ``null`` -- not an empty object -- when no adapter serves the account:
unknown is not "supports nothing" (the honest-null rule, CLAUDE.md §5).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


# --- §26.2 - §26.6 and §14.8 (M4.3) -------------------------------------------------------------

_REGION_NAME = re.compile(r"[a-z0-9-]{2,32}")
ProviderName = Literal["AWS", "AZURE", "ON_PREM", "OTHER"]
SettableStatus = Literal["ACTIVE", "DISABLED"]


def _one_line(value: str) -> str:
    value = value.strip()
    if not value or any(unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} for c in value):
        raise ValueError("must be one line of printable text")
    return value


class RegionConfiguration(BaseModel):
    """Allowed regions are an authorisation boundary (PRD §4.11), not a convenience."""

    model_config = ConfigDict(extra="forbid")

    allowed_regions: list[str] = Field(min_length=1, max_length=20)
    default_region: str

    @field_validator("allowed_regions")
    @classmethod
    def _regions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(_REGION_NAME.fullmatch(r) is None for r in value):
            raise ValueError("regions must be distinct names such as eu-west-1")
        return value

    @model_validator(mode="after")
    def _default_is_allowed(self) -> RegionConfiguration:
        if self.default_region not in self.allowed_regions:
            raise ValueError("default_region must be one of allowed_regions")
        return self


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    account_reference: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    region_configuration: RegionConfiguration
    #: A pointer, never a secret: for AWS an IAM role ARN (``OPEN-79``).
    credential_reference: str = Field(min_length=1, max_length=512)

    _display = field_validator("display_name")(_one_line)


class AccountUpdate(BaseModel):
    """Everything optional. The provider and the account reference cannot change."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    region_configuration: RegionConfiguration | None = None
    credential_reference: str | None = Field(default=None, min_length=1, max_length=512)
    status: SettableStatus | None = None

    @field_validator("display_name")
    @classmethod
    def _display(cls, value: str | None) -> str | None:
        return None if value is None else _one_line(value)


class VerificationOut(BaseModel):
    """§26.5. ``ssm:SendCommand`` is listed in ``not_probed``, never guessed (``OPEN-87``)."""

    model_config = ConfigDict(extra="forbid")

    reachable: bool
    permissions_ok: bool
    missing_permissions: list[str]
    not_probed: list[str]
    checked_at: datetime
    #: A closed code: ``ok`` or the first thing that is wrong.
    message: str


class AccountOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    provider: str
    account_reference: str
    display_name: str
    region_configuration: dict[str, Any]
    credential_reference: str | None
    #: A short digest of the reference, so a change is visible without showing a value.
    credential_fingerprint: str | None
    status: AccountStatus
    last_sync_at: datetime | None
    #: A closed code, never provider text.
    last_sync_error: str | None
    #: ``null`` = never verified (or the cache could not be read): unknown, not "fine".
    verification: VerificationOut | None
    created_at: datetime
    updated_at: datetime


class AccountList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccountOut]


class SyncAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str


class ConnectivityOut(BaseModel):
    """§14.8. ``reachable = false`` is an answer; a provider that cannot answer is an error."""

    model_config = ConfigDict(extra="forbid")

    reachable: bool
    checked_at: datetime
    provider_status: str
    data_state: Literal["ok"]
    message: str
