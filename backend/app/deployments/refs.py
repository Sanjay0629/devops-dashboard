"""Refs and ref patterns (``OPEN-122``).

A *ref* is the branch, tag or commit a requester names. It is checked against a fixed grammar
**before any administrator-written pattern runs**, so the patterns only ever see a bounded,
plain string.

A *pattern* is an administrator's regular expression (``allowed_ref_patterns``). It is matched
with ``re.fullmatch`` whatever ``^``/``$`` it carries, so ``main`` cannot admit ``main-evil``.
At registration a pattern may not use look-around, back-references, inline flags or a quantified
group. **That is a restriction, not a proof of linear time:** the bound is the 200-character cap on
the input. If a pattern language ever has to be stronger, the upgrade is an RE2 binding, which is
a new dependency and goes through ``docs/TECH_STACK.md`` section 10.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

REF_MAX_LENGTH: Final = 200
PATTERN_MAX_LENGTH: Final = 200
MAX_PATTERNS: Final = 10

_REF: Final = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_QUANTIFIERS: Final = frozenset("*+?{")


class InvalidPatternError(ValueError):
    """The message names the rule, never the pattern."""


def is_valid_ref(ref: object) -> bool:
    """Git-style ref names, a subset: the charset alone already excludes ``@{``, ``~``, ``^``,
    ``:``, ``?``, ``*``, ``[``, ``\\``, spaces and every control character (a newline included)."""
    if not isinstance(ref, str) or _REF.fullmatch(ref) is None:
        return False
    return not (
        ref[0] in "-/" or ref[-1] in "/." or ".." in ref or "//" in ref or ref.endswith(".lock")
    )


def validate_pattern(pattern: str) -> str:
    """Return ``pattern`` if it may be stored, else :class:`InvalidPatternError`."""
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= PATTERN_MAX_LENGTH:
        raise InvalidPatternError("must be 1 to 200 characters")
    if not pattern.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in pattern):
        raise InvalidPatternError("must be printable ASCII")
    _scan(pattern)
    try:
        re.compile(pattern)
    except re.error:
        raise InvalidPatternError("is not a valid regular expression") from None
    return pattern


def _scan(pattern: str) -> None:
    """Refuse look-around, back-references, inline flags and a group followed by a quantifier."""
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            nxt = pattern[i + 1] if i + 1 < n else ""
            if nxt.isdigit() or nxt in {"g", "k"}:
                raise InvalidPatternError("must not use back-references")
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            # A leading ']' (or '^]') is a literal member, not the end of the class.
            if pattern[i + 1 : i + 2] == "^":
                i += 1
            if pattern[i + 1 : i + 2] == "]":
                i += 1
        elif c == "(":
            if pattern[i + 1 : i + 2] == "?" and pattern[i + 2 : i + 3] != ":":
                raise InvalidPatternError("must not use look-around, flags or named groups")
        elif c == ")" and pattern[i + 1 : i + 2] in _QUANTIFIERS:
            raise InvalidPatternError("must not put a quantifier on a group")
        i += 1


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def ref_permitted(patterns: list[str] | tuple[str, ...], ref: object) -> bool:
    """May ``ref`` be deployed under ``patterns``? Empty ``patterns`` permit nothing.

    Fails closed: an invalid ref, or a stored pattern that does not compile, never matches.
    """
    if not isinstance(ref, str) or not is_valid_ref(ref):
        return False
    for pattern in patterns:
        compiled = _compiled(pattern)
        if compiled is not None and compiled.fullmatch(ref) is not None:
            return True
    return False
