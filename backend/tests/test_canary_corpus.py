"""One canary corpus, two redaction layers (SECURITY.md §17, M3.1).

``infra/compose/sample-logs/canaries.json`` is the contract between the backend's
``redact_text`` (Python ``re``) and the RE2 expressions in ``infra/alloy/config.alloy``.
They are two texts that must mean the same thing, and nothing but this corpus
ties them together: a pattern changed in one and not the other fails here only
if a canary exercises it.

The Alloy expressions are read out of the config and applied the way Alloy's
``stage.replace`` applies them, replacing only the capture group. Python's ``re``
is not RE2, so this is a fast tripwire, not proof: the proof is the smoke test,
which sends the same canaries through a real Alloy into a real Loki (step 18d).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.redaction import MARKER, redact_text

_ROOT = Path(__file__).parents[2]
_CORPUS = json.loads((_ROOT / "infra/compose/sample-logs/canaries.json").read_text("utf-8"))
_ALLOY = (_ROOT / "infra/alloy/config.alloy").read_text("utf-8")
_TOKEN = "<<SECRET>>"


def _lines(canary: dict) -> list[str]:
    template = canary.get("lines") or [canary["line"]]
    return [line.replace(_TOKEN, canary["secret"]) for line in template]


def _alloy_replace_stages() -> list[re.Pattern[str]]:
    """The ``stage.replace`` expressions of ``loki.process "common"``, in order."""
    block = re.search(r'loki\.process "common" \{.*', _ALLOY, re.DOTALL)
    assert block, "loki.process common not found in config.alloy"
    found = re.findall(
        r"stage\.replace \{\s*expression\s*=\s*`([^`]*)`\s*replace\s*=\s*\"\[REDACTED\]\"",
        block.group(0),
    )
    return [re.compile(expression) for expression in found]


def _alloy_redact(text: str) -> str:
    def mask(match: re.Match[str]) -> str:
        start = match.start(1) - match.start()
        end = match.end(1) - match.start()
        return match.group(0)[:start] + MARKER + match.group(0)[end:]

    for pattern in _alloy_replace_stages():
        text = pattern.sub(mask, text)
    return text


def _single_line(canary: dict) -> bool:
    return not canary.get("known_gap")


_SINGLE = [c for c in _CORPUS if _single_line(c)]
_IDS = [c["name"] for c in _SINGLE]


def test_the_alloy_config_has_the_six_redaction_classes_each_with_one_capture_group() -> None:
    patterns = _alloy_replace_stages()
    assert len(patterns) == 6
    # Replacing "the capture group" is only well defined with exactly one.
    assert [p.groups for p in patterns] == [1] * 6


@pytest.mark.parametrize("canary", _SINGLE, ids=_IDS)
def test_the_backend_redacts_every_single_line_canary(canary: dict) -> None:
    for line in _lines(canary):
        out = redact_text(line)
        assert canary["secret"] not in out, f"{canary['name']} survived redact_text: {out}"
        assert MARKER in out


@pytest.mark.parametrize("canary", _SINGLE, ids=_IDS)
def test_the_alloy_expressions_redact_every_single_line_canary(canary: dict) -> None:
    for line in _lines(canary):
        out = _alloy_redact(line)
        assert canary["secret"] not in out, f"{canary['name']} survived config.alloy: {out}"
        assert MARKER in out


@pytest.mark.parametrize("canary", _SINGLE, ids=_IDS)
def test_both_layers_redact_a_canary_inside_a_json_log_line(canary: dict) -> None:
    """The application source ships JSON lines; redaction must not break them."""
    for line in _lines(canary):
        encoded = json.dumps({"level": "error", "message": line})
        out = _alloy_redact(encoded)
        assert canary["secret"] not in out
        json.loads(out)  # still one parseable JSON document


def test_the_known_gap_is_a_gap_in_both_layers() -> None:
    """A multi-line PEM key cannot be redacted by a per-entry regex. Pinned, so
    the limit is a recorded fact (SECURITY.md §17) and cannot drift unnoticed."""
    gap = [c for c in _CORPUS if c.get("known_gap")]
    assert gap, "the corpus lost its known-gap canary"
    for canary in gap:
        leaked_by_backend = [line for line in _lines(canary) if redact_text(line) == line]
        leaked_by_alloy = [line for line in _lines(canary) if _alloy_redact(line) == line]
        assert any(canary["secret"] in line for line in leaked_by_backend)
        assert any(canary["secret"] in line for line in leaked_by_alloy)


def test_benign_text_is_left_alone_by_the_alloy_expressions() -> None:
    for line in (
        "GET /orders/42 HTTP/1.1 200 512",
        "sample-logs round 7 heartbeat",
        "worker restarted after 3 attempts",
    ):
        assert _alloy_redact(line) == line
