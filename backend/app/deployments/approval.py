"""Does a deployment need an approval? (``OPEN-133``).

One pure function, used by the request path **and** by the worker's re-check, so the two cannot
disagree. Until M5.4 builds the approval workflow, *required* means *refused*: nothing can decide
an approval, so a request that needs one is not queued behind a decision that cannot come.

It fails closed: a linked policy (whatever its row now says -- the caller does not read it, so a
missing or unreadable one is *required* too) and any environment that is not one of the three
non-production names need an approval. ``production`` is not a special case in the code but the
absence of a reason to say no.
"""

from __future__ import annotations

from typing import Final

#: The closed reason a request or a worker refusal carries (``details.reason``).
APPROVAL_WORKFLOW_UNAVAILABLE: Final = "approval_workflow_unavailable"
NON_PRODUCTION: Final[frozenset[str]] = frozenset({"development", "staging", "other"})


def approval_required(*, environment: str, policy_linked: bool) -> bool:
    """True when the target has an approval policy, or the environment is not non-production."""
    return policy_linked or environment not in NON_PRODUCTION
