"""The target digest (``OPEN-135``, ``OPEN-137``).

A deployment stores the digest of the pipeline reference and parameter template its requester was
shown; the worker recomputes it from the live target before it triggers anything and refuses
(``TARGET_CHANGED``) on a mismatch, so an administrator's edit between the request and the trigger
cannot make Jenkins run something nobody saw.

The input is a **structured** encoding (sorted-key canonical JSON of both values together), not a
concatenation, so text moved from one value into the other cannot produce the same digest. Both
sides read the value from the same ``jsonb`` column, so key order is irrelevant and is sorted away.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def target_digest(pipeline_reference: str, parameter_template: Mapping[str, Any]) -> str:
    """SHA-256 hex over the canonical JSON of the two values. Raises ``ValueError`` on NaN/inf."""
    canonical = json.dumps(
        {"pipeline_reference": pipeline_reference, "parameter_template": parameter_template},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
