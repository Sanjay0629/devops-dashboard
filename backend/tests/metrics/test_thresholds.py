"""Threshold classification (M2.2): the boundary values the plan asks for."""

from __future__ import annotations

import pytest

from app.metrics import templates, thresholds
from app.metrics.thresholds import classify, is_stale

CPU = {"warning": 80.0, "critical": 90.0}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "normal"),
        (79.999, "normal"),
        # The line itself is on the wrong side of it.
        (80.0, "warning"),
        (89.999, "warning"),
        (90.0, "critical"),
        (100.0, "critical"),
        # Utilisation can be reported above 100 by a rate over a short window.
        (250.0, "critical"),
    ],
)
def test_the_boundaries(value: float, expected: str) -> None:
    assert classify(CPU, value) == expected


def test_no_value_is_not_a_normal_value() -> None:
    """An unreadable host is unknown, not well (PRD §4.2)."""
    assert classify(CPU, None) is None


def test_no_threshold_is_not_a_zero_threshold() -> None:
    """Seven templates have none, and 0 would make every value 'critical'."""
    assert classify(None, 12.0) is None
    assert classify(None, None) is None


def test_zero_is_a_real_value() -> None:
    assert classify(CPU, 0.0) == "normal"


def test_every_catalogue_default_is_ordered_and_complete() -> None:
    """``classify`` indexes both keys, so a malformed default must fail here."""
    for template in templates.CATALOGUE:
        if template.default_thresholds is None:
            continue
        assert set(template.default_thresholds) == {"warning", "critical"}, template.id
        assert template.default_thresholds["warning"] < template.default_thresholds["critical"]


def test_staleness_bound_is_inclusive_of_the_limit() -> None:
    assert not is_stale(0)
    assert not is_stale(thresholds.STALE_AFTER_SECONDS)
    assert is_stale(thresholds.STALE_AFTER_SECONDS + 0.001)


def test_the_bound_is_below_prometheuss_lookback() -> None:
    """Otherwise ``stale`` is unreachable: the series vanishes at 300 s first."""
    assert thresholds.STALE_AFTER_SECONDS < 300
