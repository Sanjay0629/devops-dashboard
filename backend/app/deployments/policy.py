"""The success policy (``OPEN-138``; AD-10, CLAUDE.md §4 rule 8, PRD §4.10).

A deployment succeeds only when the pipeline reported ``SUCCESS`` **and** the recorded health
result passes. Until M5.5 no target can register a health check, so the result recorded is the
explicit "none configured" one, never a bare ``{}``: the schema refuses ``SUCCEEDED`` with ``{}``
(issue 197a), and a page must be able to say *why* nothing was checked.

:func:`success` is deliberately suspicious of its input. It is the last thing between a malformed
result and a green "succeeded", so anything it does not recognise is a failure, not a pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

POLICY: Final = "all_must_pass"
#: Shown beside a success that nothing but the pipeline backs (``OPEN-138``).
NONE_CONFIGURED_STATEMENT: Final = (
    "No post-deployment health checks are configured for this target; "
    "success means only that the pipeline succeeded."
)


def none_configured() -> dict[str, Any]:
    """The explicit result for a target with no checks. A fresh object each call."""
    return {"policy": POLICY, "configured": False, "checks": [], "passed": True}


def success(pipeline_result: str | None, health: Mapping[str, Any] | None) -> bool:
    """True only for a pipeline ``SUCCESS`` and a well-formed result that passes.

    ``UNSTABLE`` is not success. A result that says ``passed`` over a check that did not is not a
    pass, nor is ``configured: true`` with no check having run, nor a ``passed`` that is merely
    truthy.
    """
    if pipeline_result != "SUCCESS":
        return False
    if not isinstance(health, Mapping) or health.get("policy") != POLICY:
        return False
    configured, checks = health.get("configured"), health.get("checks")
    if not isinstance(configured, bool) or not isinstance(checks, list):
        return False
    if health.get("passed") is not True:
        return False
    if not configured:
        return checks == []
    return bool(checks) and all(
        isinstance(check, Mapping) and check.get("healthy") is True for check in checks
    )
