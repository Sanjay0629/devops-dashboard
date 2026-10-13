"""Auth response schemas (docs/API_CONTRACTS.md §12.4).

``extra="forbid"`` on everything, per CLAUDE.md §5 — these are responses, but
the same model classes are the contract the generated TypeScript types come
from, and a silently ignored field is a contract bug in either direction.

What these models deliberately do **not** contain: any token, any session
identifier, any issuer or subject. The response describes the user, not how
they authenticated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    email: str
    display_name: str
    status: str


class SessionRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    #: The grant's scope, as stored. Shown so an operator can see why the UI
    #: offers what it offers; it is not an input to any decision.
    scope: dict[str, Any] = Field(default_factory=dict)


class SessionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The absolute deadline — the latest this session can possibly live.
    #: RFC 3339 UTC (docs/API_CONTRACTS.md §1).
    expires_at: datetime
    idle_timeout_seconds: int


class SessionResponse(BaseModel):
    """The identity the UI renders from.

    ``permissions`` exists to render the UI and **carries no authority**
    (mandatory rule 5, docs/API_CONTRACTS.md §12.4). Every action is
    independently authorised server-side; this array is allowed to be wider
    than what the caller can actually do, because it is scope-blind.
    """

    model_config = ConfigDict(extra="forbid")

    user: SessionUser
    roles: list[SessionRole]
    permissions: list[str]
    session: SessionWindow
    demo_mode: bool
    #: Always false for now: read-only mode is a global switch introduced with
    #: the write endpoints in M1.5. Present from the start so the frontend
    #: contract does not change when it becomes real.
    read_only_mode: bool = False


class LogoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Idempotent by contract: logging out twice succeeds (§12.3), so this is
    #: always true and does not report whether a session was actually present.
    #: Reporting that would leak whether a stolen cookie was still live.
    logged_out: bool = True
