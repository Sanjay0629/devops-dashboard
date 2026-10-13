"""Shared column types and identifier generation.

UUIDv7 (RFC 9562) rather than v4: the first 48 bits are a millisecond timestamp,
so generated ids sort by creation time. On the append-heavy tables — especially
the partitioned ``audit_events`` — that keeps index inserts at the right-hand
edge of the B-tree instead of scattering them, which is the difference between
appending to one page and dirtying a random page per row.

``uuid.uuid7`` only arrived in the standard library in Python 3.14 and this
project pins 3.12, so the twelve lines below stand in for it. Ids are opaque to
every consumer, so swapping the generator later needs no migration.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Annotated

from sqlalchemy import CheckConstraint, String, text
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP
from sqlalchemy.orm import mapped_column

_UUID7_VARIANT = 0b10
_UUID7_VERSION = 0x7


def uuid7() -> uuid.UUID:
    """Return a time-ordered UUID (RFC 9562 version 7).

    Layout: 48-bit big-endian millisecond timestamp, 4-bit version, 12 bits of
    randomness, 2-bit variant, 62 bits of randomness. Monotonic *per
    millisecond* only — two ids minted in the same millisecond order randomly
    relative to each other, which is fine for index locality and is never
    relied upon for ordering. ``created_at`` is what orders rows.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    random_bits = int.from_bytes(os.urandom(10), "big")

    value = (timestamp_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= _UUID7_VERSION << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= _UUID7_VARIANT << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)


# --- reusable annotated column types ---------------------------------------
# Timestamps are always timezone-aware. A naive timestamp in an operational
# timeline spanning regions is a bug, not a simplification
# (docs/DATABASE.md §1 rule 3).

Timestamp = Annotated[object, mapped_column(TIMESTAMP(timezone=True))]


def utcnow_server_default() -> object:
    """``now()`` as a server default, so the database owns the clock."""
    return text("now()")


def jsonb_column(*, nullable: bool = False, default: str = "'{}'::jsonb") -> object:
    return mapped_column(JSONB, nullable=nullable, server_default=text(default))


def enum_check(column: str, allowed: tuple[str, ...], name: str) -> CheckConstraint:
    """A closed value set as a CHECK constraint rather than a native ENUM.

    Adding a value to a native PostgreSQL enum needs a migration that cannot
    always run inside a transaction; a CHECK is editable and shows up in a
    reviewable diff (docs/DATABASE.md §1 rule 5).
    """
    values = ", ".join(f"'{value}'" for value in allowed)
    return CheckConstraint(f"{column} IN ({values})", name=name)


ShortText = Annotated[str, mapped_column(String(128))]
MediumText = Annotated[str, mapped_column(String(256))]
LongText = Annotated[str, mapped_column(String(1024))]

__all__ = [
    "INET",
    "JSONB",
    "TIMESTAMP",
    "LongText",
    "MediumText",
    "ShortText",
    "Timestamp",
    "enum_check",
    "jsonb_column",
    "utcnow_server_default",
    "uuid7",
]
