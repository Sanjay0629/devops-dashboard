"""The "Prometheus last answered" marker (M2.5 D2), without a Redis.

The marker is what keeps a Prometheus outage from ageing the whole fleet: hosts
are aged against the last moment Prometheus *answered*, so while it is down the
clock stops. Everything that can go wrong with it must read as "no reference",
which the sweep treats as "judge nothing" — never as a time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from redis.exceptions import RedisError

from app.monitoring import sweep

AT = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self, value: Any = None, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.sets: list[tuple[str, str]] = []

    def get(self, key: str) -> Any:
        if self.fail:
            raise RedisError("down")
        return self.value

    def set(self, key: str, value: str) -> None:
        if self.fail:
            raise RedisError("down")
        self.sets.append((key, value))


def test_a_written_marker_reads_back_as_the_same_instant() -> None:
    client = FakeRedis()
    sweep.write_marker(client, AT)
    key, value = client.sets[0]
    assert key == sweep.MARKER_KEY
    assert sweep.read_marker(FakeRedis(value.encode())) == AT


@pytest.mark.parametrize(
    "stored",
    [None, b"", b"not a time", b"2026-10-17T12:00:00", b"\xff\xfe", 12345],
)
def test_anything_unreadable_is_no_reference(stored: Any) -> None:
    """Missing, garbage, or a *naive* time: all "unknown", none of them a time.

    A naive timestamp is refused because aging against a guessed zone is how a
    host gets called unreachable by an hour.
    """
    assert sweep.read_marker(FakeRedis(stored)) is None


def test_a_redis_failure_on_read_is_no_reference_not_an_error() -> None:
    assert sweep.read_marker(FakeRedis(fail=True)) is None


def test_a_redis_failure_on_write_is_swallowed() -> None:
    """The probe must still write its own results; the marker is best effort.

    A stale marker only makes the sweep more conservative.
    """
    sweep.write_marker(FakeRedis(fail=True), AT)
