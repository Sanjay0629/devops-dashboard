"""Request and response models for §21.8 – §21.12 (``OPEN-126``).

Every request model is ``extra="forbid"`` and **has no field for a Jenkins URL, a node label, a
credential or a command** -- the two administrator-written fields that become Jenkins requests
(``pipeline_reference`` and the build-parameter template) are validated against closed grammars
here and are never concatenated into a URL as written (``app.deployments.paths``).

The reader shape (:class:`TargetSummary`) deliberately lacks the parameter template and the
approval-policy id; an ``admin:integrations`` holder gets :class:`TargetDetail`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.deployments import paths, refs
from app.deployments.template import ParameterTemplate, ref_slot_problem
from app.inventory.schemas import CursorPage, Environment

TargetStatus = Literal["ACTIVE", "DISABLED"]
NOT_YET = "post-deployment health checks arrive in a later milestone (M5.5)"


def _job_path(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return paths.validate_job_path(value)
    except paths.InvalidJobPathError as error:
        raise ValueError(str(error)) from None


def _patterns(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    if len(set(values)) != len(values):
        raise ValueError("patterns must be distinct")
    for pattern in values:
        try:
            refs.validate_pattern(pattern)
        except refs.InvalidPatternError as error:
            raise ValueError(str(error)) from None
    return values


def cross_field_problems(
    template: ParameterTemplate,
    patterns: list[str],
    rollback_supported: bool,
    rollback_reference: str | None,
) -> list[tuple[str, str]]:
    """The rules that tie fields together, as ``(field, message)``. Empty means they hold."""
    problems: list[tuple[str, str]] = []
    if (message := ref_slot_problem(template, patterns)) is not None:
        problems.append(("parameter_template", message))
    if rollback_supported != (rollback_reference is not None):
        problems.append(
            (
                "rollback_pipeline_reference",
                "must be given exactly when rollback_supported is true",
            )
        )
    return problems


class TargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    environment: Environment
    pipeline_reference: str = Field(max_length=324)
    parameter_template: ParameterTemplate = Field(default_factory=ParameterTemplate)
    allowed_ref_patterns: list[str] = Field(default_factory=list, max_length=refs.MAX_PATTERNS)
    approval_policy_id: uuid.UUID | None = None
    #: ``{}`` until M5.5; anything else is ``422 not_permitted`` (``OPEN-124``), checked by the
    #: service so the answer carries that code rather than a generic validation failure.
    health_check_config: dict[str, Any] = Field(default_factory=dict)
    rollback_supported: bool = False
    rollback_pipeline_reference: str | None = Field(default=None, max_length=324)

    _check_path = field_validator("pipeline_reference", "rollback_pipeline_reference")(_job_path)
    _check_patterns = field_validator("allowed_ref_patterns")(_patterns)

    @model_validator(mode="after")
    def _cross_field(self) -> Self:
        for field, message in cross_field_problems(
            self.parameter_template,
            self.allowed_ref_patterns,
            self.rollback_supported,
            self.rollback_pipeline_reference,
        ):
            raise ValueError(f"{field}: {message}")
        return self


class TargetUpdate(BaseModel):
    """Only these fields can change. ``application_id``, ``environment`` and
    ``health_check_config`` are absent, so ``extra="forbid"`` refuses them."""

    model_config = ConfigDict(extra="forbid")

    pipeline_reference: str | None = Field(default=None, max_length=324)
    parameter_template: ParameterTemplate | None = None
    allowed_ref_patterns: list[str] | None = Field(default=None, max_length=refs.MAX_PATTERNS)
    #: Explicit ``null`` clears the policy (allowed except for production, which the service
    #: refuses); an absent key leaves it alone.
    approval_policy_id: uuid.UUID | None = None
    rollback_supported: bool | None = None
    rollback_pipeline_reference: str | None = Field(default=None, max_length=324)
    status: TargetStatus | None = None

    _check_path = field_validator("pipeline_reference", "rollback_pipeline_reference")(_job_path)
    _check_patterns = field_validator("allowed_ref_patterns")(_patterns)

    @model_validator(mode="after")
    def _no_null_where_a_value_is_needed(self) -> Self:
        for name in ("pipeline_reference", "parameter_template", "allowed_ref_patterns", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        if "rollback_supported" in self.model_fields_set and self.rollback_supported is None:
            raise ValueError("rollback_supported must not be null")
        return self


class TargetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    application_id: uuid.UUID
    application_name: str
    environment: Environment
    pipeline_reference: str
    allowed_ref_patterns: list[str]
    requires_approval: bool
    rollback_supported: bool
    status: TargetStatus
    updated_at: datetime


class TargetDetail(TargetSummary):
    approval_policy_id: uuid.UUID | None
    parameter_template: ParameterTemplate
    health_check_config: dict[str, Any]
    rollback_pipeline_reference: str | None
    created_at: datetime


class TargetCreated(TargetDetail):
    idempotent_replay: bool = False


class JenkinsConfiguration(BaseModel):
    """A fact about configuration, **not a probe**: §26.1 is the live check."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_configured", "configured"]
    demo_mode: bool


class TargetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TargetSummary]
    page: CursorPage
    jenkins: JenkinsConfiguration


TargetIdParam = Annotated[uuid.UUID, Field(description="Deployment target identifier")]
