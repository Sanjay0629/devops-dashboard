"""The preset catalogue: closed, portable to RE2, and each preset does its job."""

from __future__ import annotations

import re
from typing import get_args

from app.logs.presets import BY_ID, PRESETS
from app.logs.schemas import PresetId

#: One or more lines each preset must match. The same lines were run through a
#: real Loki 3.3.2 in the M3.2 spike; every preset matched exactly its own.
POSITIVE = {
    "http_5xx": [
        '1.2.3.4 - - [x] "GET /o HTTP/1.1" 502 12 "-" "a"',
        '{"status": 503, "m": 1}',
    ],
    "oom_killed": ["Out of memory: Killed process 12 (php)"],
    "connection_refused": ["connect() failed (111: Connection refused) while connecting"],
    "timeout": ["upstream timed out", "context deadline exceeded"],
    "permission_denied": ["open() failed: Permission denied"],
    "auth_failure": ["Failed password for root from 1.2.3.4"],
    "segfault": ["child 5 exited on signal 11 (SIGSEGV)"],
    "disk_full": ["write failed: No space left on device"],
}
#: Lines that must match no preset at all.
NEGATIVE = [
    '1.2.3.4 - - [x] "GET /o HTTP/1.1" 200 5000 "-" "a"',
    '{"status": 200}',
    "all good",
    'GET /x HTTP/1.1" 404 502 ',
    "sample-logs round 7 request 5a3f",
]


def test_the_catalogue_and_the_request_literal_list_the_same_ids() -> None:
    assert set(BY_ID) == set(get_args(PresetId))
    assert len(BY_ID) == len(PRESETS) == 8


def test_every_preset_has_a_positive_line() -> None:
    assert set(POSITIVE) == set(BY_ID)


def test_each_preset_matches_its_own_positive_lines() -> None:
    for preset_id, lines in POSITIVE.items():
        pattern = re.compile(BY_ID[preset_id].regex)
        for line in lines:
            assert pattern.search(line), (preset_id, line)


def test_no_preset_matches_a_negative_line() -> None:
    for preset in PRESETS:
        pattern = re.compile(preset.regex)
        for line in NEGATIVE:
            assert not pattern.search(line), (preset.id, line)


def test_presets_stay_inside_what_re2_accepts() -> None:
    """Loki runs RE2: no lookaround, no backreferences, no possessive quantifiers."""
    for preset in PRESETS:
        assert not re.search(r"\(\?[=!<]|\\[1-9]|[*+?}]\+", preset.expression), preset.id


def test_every_preset_is_case_insensitive_and_labelled() -> None:
    for preset in PRESETS:
        assert preset.regex.startswith("(?i)")
        assert preset.label and preset.description
