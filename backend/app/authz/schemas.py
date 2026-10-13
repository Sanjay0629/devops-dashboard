"""Scope validation at the trust boundary (CLAUDE.md §5, docs/DATABASE.md §3.5).

The database stores ``user_roles.scope_definition`` as ``jsonb`` and does not
constrain its shape, deliberately — the shape is an application concern that
would otherwise need a migration to change. That makes this module the only
thing standing between a request body and a stored grant, so it is strict:
closed key set, closed environment values, UUID-or-``*`` elsewhere, no empty
lists, no duplicates.

What it does **not** do is decide anything. A scope that parses here can still
grant nothing (``{}``) or be refused for production reasons; the matcher owns
that (``app/authz/matcher.py``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.approvals.models import ENVIRONMENTS
from app.authz.matcher import SCOPE_DIMENSIONS, WILDCARD

#: ``"*"`` or a UUID string, for the dimensions that reference rows.
_REFERENCE_DIMENSIONS = tuple(d for d in SCOPE_DIMENSIONS if d != "environment")


class ScopeDefinition(BaseModel):
    """``{"environment": ["staging"], "server": ["*"], …}``.

    Every dimension is optional, and one that is absent is unconstrained —
    except for production, which a grant reaches only by naming it
    (docs/SECURITY.md §4 rule 4, enforced in the matcher rather than here: a
    scope naming no environment is perfectly valid for non-production reach).
    """

    model_config = ConfigDict(extra="forbid")

    environment: list[str] | None = Field(default=None, min_length=1, max_length=8)
    cloud_account: list[str] | None = Field(default=None, min_length=1, max_length=64)
    server: list[str] | None = Field(default=None, min_length=1, max_length=256)
    application: list[str] | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("environment")
    @classmethod
    def _known_environments(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        allowed = {*ENVIRONMENTS, WILDCARD}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown environment(s): {', '.join(unknown)}")
        return sorted(set(values))

    @field_validator(*_REFERENCE_DIMENSIONS)
    @classmethod
    def _uuid_or_wildcard(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        for value in values:
            if value == WILDCARD:
                continue
            try:
                uuid.UUID(value)
            except ValueError as error:
                # The offending value is not echoed back by the error handler
                # (docs/API_CONTRACTS.md §8); this message names the rule only.
                raise ValueError('values must be a UUID or "*"') from error
        return sorted(set(values))

    @model_validator(mode="after")
    def _wildcard_is_alone(self) -> ScopeDefinition:
        """``["*", "<uuid>"]`` is a contradiction, and most likely a mistake."""
        for dimension in SCOPE_DIMENSIONS:
            values = getattr(self, dimension)
            if values and WILDCARD in values and len(values) > 1:
                raise ValueError(f'{dimension}: "*" cannot be combined with specific values')
        return self

    def as_stored(self) -> dict[str, Any]:
        """The JSONB form: only the dimensions actually supplied."""
        return {
            dimension: values
            for dimension in SCOPE_DIMENSIONS
            if (values := getattr(self, dimension)) is not None
        }


#: Reusable annotation for request bodies that carry a scope.
ScopeField = Annotated[ScopeDefinition, Field(description="Conjunctive scope dimensions")]
