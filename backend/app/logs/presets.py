"""The pattern-preset catalogue (docs/API_CONTRACTS.md §19.1, ``OPEN-35``).

PRD §4.7 asks for "search for common error patterns". The client may not send a
regex (AD-05), so the regex power lives here, server-side, behind a closed id:
a preset can *narrow* a query and can never select a target, a label or a
server. The ids and wording are the engineering team's, not the PRD's -- a
reviewable assumption, recorded as ``OPEN-35``.

Each expression is one RE2 pattern (what Loki runs). It is applied
case-insensitively, as a line filter, ANDed with ``contains`` if both are given.
``tests/logs/test_presets.py`` holds positive and negative lines per preset, and
the same expressions were run through a real Loki 3.3.2 in the M3.2 spike: all
eight accepted, each matching exactly its positive lines.

Adding a preset means adding it here, to the ``PresetId`` literal in
``app/logs/schemas.py``, and a positive and a negative line to the test; the test
fails if any of the three is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    label: str
    description: str
    #: RE2, matched case-insensitively against the whole log line.
    expression: str

    @property
    def regex(self) -> str:
        """What goes into the ``|~`` filter."""
        return f"(?i){self.expression}"


PRESETS: Final[tuple[Preset, ...]] = (
    Preset(
        "http_5xx",
        "HTTP 5xx responses",
        "Nginx access lines with a 5xx status, and JSON lines with a 5xx status field.",
        r'HTTP/[0-9.]+" 5[0-9]{2} |"status": ?5[0-9]{2}\b',
    ),
    Preset(
        "oom_killed",
        "Out of memory / OOM kill",
        "The kernel or a runtime killing a process for memory.",
        r"out of memory|oom-kill|oom_kill|killed process|oomkilled",
    ),
    Preset(
        "connection_refused",
        "Connection refused",
        "A connect that was actively refused.",
        r"connection refused|econnrefused|connect\(\) failed",
    ),
    Preset(
        "timeout",
        "Timeouts",
        "A call that timed out or exceeded its deadline.",
        r"timed out|timeout|deadline exceeded",
    ),
    Preset(
        "permission_denied",
        "Permission denied",
        "A filesystem or access refusal.",
        r"permission denied|eacces|access denied",
    ),
    Preset(
        "auth_failure",
        "Authentication failures",
        "Failed logins and rejected credentials.",
        r"authentication fail|failed password|invalid (user|credentials)|login failed|unauthorized",
    ),
    Preset(
        "segfault",
        "Segmentation faults",
        "A process crashing on an invalid memory access.",
        r"segfault|segmentation fault|sigsegv",
    ),
    Preset(
        "disk_full",
        "Disk full",
        "A write refused for lack of space.",
        r"no space left on device|disk full|enospc",
    ),
)

BY_ID: Final[dict[str, Preset]] = {preset.id: preset for preset in PRESETS}
