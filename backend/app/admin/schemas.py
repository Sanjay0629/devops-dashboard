"""Request and response models for user and role administration (§25).

``extra="forbid"`` everywhere: an ignored field in a privilege-granting request
is a contract bug with security consequences — a client that thinks it sent
``expires_at`` and was silently ignored has created a permanent grant.

The response models are the contract's machine-readable form
(docs/API_CONTRACTS.md §31 assumption 5), since §25 specifies permissions,
validation and audit per endpoint but only one body shape.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.models import USER_STATUSES
from app.authz.models import ROLE_NAMES
from app.authz.schemas import ScopeDefinition

#: An address, bounded and minimally shaped. Not ``EmailStr``: that needs the
#: ``email-validator`` package, and docs/TECH_STACK.md §10 rung 6 applies —
#: this value is display and notification routing only, never an authorisation
#: input (docs/DATABASE.md §3.1), and the IdP is what actually attests it.
EmailAddress = Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")]

RoleName = Literal["VIEWER", "DEVELOPER", "DEVOPS_ENGINEER", "APPROVER", "ADMINISTRATOR"]
UserStatus = Literal["ACTIVE", "DISABLED"]

# The Literals above must stay in step with the database CHECK constraints.
# Checked here rather than in a test, and raising rather than asserting: a
# mismatch between what the API accepts and what the database allows is a
# startup error, and an `assert` would vanish under `python -O`.
if set(ROLE_NAMES) != set(get_args(RoleName)) or set(USER_STATUSES) != set(get_args(UserStatus)):
    raise RuntimeError(
        "admin schema enums have drifted from the database CHECK constraints; "
        "update app/admin/schemas.py and the migration together"
    )


class OffsetPage(BaseModel):
    """Offset pagination, per docs/API_CONTRACTS.md §5 for small collections."""

    model_config = ConfigDict(extra="forbid")

    limit: int
    offset: int
    total: int
    has_more: bool


class RoleGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    role_name: str
    scope_definition: dict[str, list[str]]
    granted_by: uuid.UUID
    expires_at: datetime | None
    created_at: datetime


class UserSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    email: str
    display_name: str
    status: str
    last_login_at: datetime | None
    created_at: datetime


class UserDetail(UserSummary):
    """A user plus their grants (§25.3).

    Carries the identity provider's issuer and subject: an administrator
    resolving a duplicate identity needs to see them, and neither is a secret
    (they are stable public identifiers, not credentials).
    """

    identity_provider_issuer: str
    identity_provider_subject: str
    roles: list[RoleGrant]


class UserList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UserSummary]
    page: OffsetPage


class CreateUserRequest(BaseModel):
    """§25.2 — pre-create a user so roles can be granted before first login.

    ``identity_provider_subject`` is **required** because the column is, and
    because the alternative — keying a pre-created row on email and adopting it
    at first login — is the identity merge that assumption ``A-05``
    deliberately refuses. An administrator reads the subject from the IdP
    (``oid``, ``sub``, object id) exactly as it will be presented.

    ``identity_provider_issuer`` defaults to the configured issuer, which is
    the only one this deployment accepts tokens from anyway.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    identity_provider_subject: Annotated[str, Field(min_length=1, max_length=256)]
    identity_provider_issuer: Annotated[str | None, Field(default=None, max_length=512)] = None


class UpdateUserRequest(BaseModel):
    """§25.4 — enable/disable and display name. Nothing else is editable.

    Not editable on purpose: the email comes from the IdP on every login and
    would be overwritten, and the identity key must never be edited — editing
    it would silently point one person's audit history at another's account.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str | None, Field(default=None, min_length=1, max_length=256)] = None
    status: UserStatus | None = None


class GrantRoleRequest(BaseModel):
    """§25.5, verbatim: role name, scope, optional expiry."""

    model_config = ConfigDict(extra="forbid")

    role_name: RoleName
    scope_definition: ScopeDefinition
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _in_the_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("expires_at must carry a timezone offset")
        if value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value


class RolePermissionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission: str
    effect: str


class RoleDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    permissions: list[RolePermissionEntry]


class RoleList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RoleDetail]


class PermissionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission: str
    resource_type: str
    action: str
    description: str


class PermissionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PermissionEntry]


class TerminateSessionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: How many server-side sessions were destroyed. Zero is a success: the
    #: user simply had none live.
    terminated: int


class ReadOnlyModeRequest(BaseModel):
    """§25.10 — the incident containment switch (docs/SECURITY.md §27)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    #: Required when turning it **on**: an incident switch with no stated cause
    #: is an unexplained outage to whoever finds it later. Goes into the audit
    #: row, so it is operator-supplied text and is treated as data.
    reason: Annotated[str | None, Field(default=None, min_length=1, max_length=512)] = None


class ReadOnlyModeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_only_mode: bool
