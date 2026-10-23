"""The build-parameter template (``OPEN-121``) and the one function that renders it.

A target's ``parameter_template`` is ``{"fixed": {name: string}, "slots": {name: source}}``.
``fixed`` values are written by an administrator; a ``slot`` is filled by DCP from a **closed**
set of sources. **No request value reaches Jenkins except a validated ``ref``** (and ``ref`` only
after ``app.deployments.refs``): there is no slot that means "whatever the caller sent".

The template is returned to administrators and stored in the database, so a secret has no
business in it: a ``fixed`` value that redaction would alter is refused, and a name that looks
sensitive (``API_TOKEN``) has its value masked by redaction and is refused for the same reason.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.redaction import redact_value

SlotSource = Literal["ref", "deployment_id", "environment", "requested_by"]
SLOT_SOURCES: Final[tuple[str, ...]] = ("ref", "deployment_id", "environment", "requested_by")

MAX_PARAMETERS: Final = 20
VALUE_MAX_LENGTH: Final = 256
_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _check_name(name: str) -> str:
    if _NAME.fullmatch(name) is None:
        raise ValueError("parameter names must match [A-Za-z_][A-Za-z0-9_]{0,63}")
    return name


class ParameterTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed: dict[str, str] = Field(default_factory=dict)
    slots: dict[str, SlotSource] = Field(default_factory=dict)

    @field_validator("fixed")
    @classmethod
    def _fixed_values(cls, value: dict[str, str]) -> dict[str, str]:
        for name, text in value.items():
            _check_name(name)
            if len(text) > VALUE_MAX_LENGTH:
                raise ValueError("fixed values must be at most 256 characters")
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError("fixed values must not contain control characters")
            if redact_value({name: text}) != {name: text}:
                raise ValueError(
                    "fixed values must not look like a secret; a secret belongs in the "
                    "secret store, not in a template"
                )
        return value

    @field_validator("slots")
    @classmethod
    def _slot_names(cls, value: dict[str, SlotSource]) -> dict[str, SlotSource]:
        for name in value:
            _check_name(name)
        return value

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if set(self.fixed) & set(self.slots):
            raise ValueError("a parameter name may appear once, as fixed or as a slot")
        if len(self.fixed) + len(self.slots) > MAX_PARAMETERS:
            raise ValueError("at most 20 parameters")
        return self

    @property
    def ref_slots(self) -> list[str]:
        return [name for name, source in self.slots.items() if source == "ref"]


def ref_slot_problem(template: ParameterTemplate, patterns: list[str]) -> str | None:
    """The rule tying ``ref`` to ``allowed_ref_patterns``, or ``None`` if it holds.

    With patterns there must be exactly one ``ref`` slot (otherwise DCP would validate a ref that
    never reaches Jenkins); without patterns there must be none.
    """
    count = len(template.ref_slots)
    if patterns and count != 1:
        return "exactly one parameter must be filled from 'ref' when ref patterns are set"
    if not patterns and count:
        return "no parameter may be filled from 'ref' when no ref patterns are set"
    return None


def render_parameters(
    template: ParameterTemplate | Mapping[str, object],
    *,
    ref: str | None,
    deployment_id: uuid.UUID,
    environment: str,
    requested_by: uuid.UUID,
) -> dict[str, str]:
    """The build parameters for one deployment. Nothing else can enter the result.

    ``ref`` must already have been validated (``refs.ref_permitted``). A template with a ``ref``
    slot and no ``ref`` is an error rather than an empty parameter.
    """
    parsed = (
        template
        if isinstance(template, ParameterTemplate)
        else ParameterTemplate.model_validate(template)
    )
    values: dict[str, str] = {
        "deployment_id": str(deployment_id),
        "environment": environment,
        "requested_by": str(requested_by),
    }
    if ref is not None:
        values["ref"] = ref
    rendered = dict(parsed.fixed)
    for name, source in parsed.slots.items():
        if source not in values:
            raise ValueError("a 'ref' slot needs a validated ref")
        rendered[name] = values[source]
    return rendered
