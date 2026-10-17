"""Host health from ``last_seen_at`` (M2.5 commit 1): the full time matrix.

The classifier is a pure function of three things — thresholds, when the host
was last seen, and the reference time the age is measured against — so every
case here is a literal, with no clock and no database.

**A state begins strictly after its threshold.** A host exactly on a line is
the oldest one still on the better side of it, the same rule as the existing
``stale`` flag (``is_stale``: "a sample exactly at the bound is the oldest still
counted as current"). The boundary cases below pin that at -1 s, 0 s and +1 s.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.approvals.models import ENVIRONMENTS
from app.core.config import ConfigurationError, Settings, load_settings
from app.monitoring.health import (
    HealthThresholds,
    HealthThresholdTable,
    classify_health,
)

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
DEFAULTS = HealthThresholds()  # AD-14: 30 s / 5 min / 15 min


def seen(seconds_ago: float) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


@pytest.mark.parametrize(
    ("age", "state", "data_state"),
    [
        (0, "healthy", "ok"),
        (29, "healthy", "ok"),
        (30, "healthy", "ok"),  # on the line: still the better side
        (31, "warning", "ok"),
        (299, "warning", "ok"),
        (300, "warning", "ok"),
        (301, "stale", "stale"),
        (899, "stale", "stale"),
        (900, "stale", "stale"),
        (901, "unreachable", "stale"),
        (86_400, "unreachable", "stale"),
    ],
)
def test_the_time_matrix(age: float, state: str, data_state: str) -> None:
    result = classify_health(DEFAULTS, seen(age), NOW)
    assert (result.state, result.data_state) == (state, data_state)


def test_never_seen_is_unknown_not_stale() -> None:
    """No observation is not an old observation (PRD §4.2, AD-14)."""
    result = classify_health(DEFAULTS, None, NOW)
    assert (result.state, result.data_state) == ("unknown", "unavailable")


def test_no_reference_time_is_unknown_never_stale_or_unreachable() -> None:
    """D2: with no proof Prometheus answered, a host's age cannot be judged.

    Aging a host against a clock nobody confirmed is how a Prometheus outage
    turns into a fleet outage (PROGRESS issue 67).
    """
    result = classify_health(DEFAULTS, seen(10_000), None)
    assert (result.state, result.data_state) == ("unknown", "unavailable")


def test_a_last_seen_after_the_reference_is_fresh_not_an_error() -> None:
    """Two writers, two clocks: a skew of a second must not flip a host."""
    result = classify_health(DEFAULTS, NOW + timedelta(seconds=5), NOW)
    assert (result.state, result.data_state) == ("healthy", "ok")


def test_fractional_seconds_are_not_rounded_across_a_line() -> None:
    assert classify_health(DEFAULTS, seen(30.001), NOW).state == "warning"
    assert classify_health(DEFAULTS, seen(900.001), NOW).state == "unreachable"


def test_a_naive_datetime_is_refused_rather_than_guessed() -> None:
    """Fail closed: comparing naive to aware raises, it never guesses a zone."""
    with pytest.raises(TypeError):
        classify_health(DEFAULTS, NOW.replace(tzinfo=None), NOW)


def test_custom_thresholds_move_every_line() -> None:
    tight = HealthThresholds(
        warning_after_seconds=10, stale_after_seconds=20, unreachable_after_seconds=40
    )
    assert classify_health(tight, seen(10), NOW).state == "healthy"
    assert classify_health(tight, seen(11), NOW).state == "warning"
    assert classify_health(tight, seen(21), NOW).state == "stale"
    assert classify_health(tight, seen(41), NOW).state == "unreachable"


@pytest.mark.parametrize(
    "values",
    [
        {"warning_after_seconds": 300, "stale_after_seconds": 300},  # equal
        {"stale_after_seconds": 900, "unreachable_after_seconds": 600},  # inverted
        {"warning_after_seconds": 0},
        {"warning_after_seconds": -1},
        {"unreachable_after_seconds": 86_401},
    ],
)
def test_thresholds_must_be_positive_bounded_and_strictly_ordered(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        HealthThresholds(**values)


# --- per-environment resolution -----------------------------------------------


def test_an_empty_table_is_the_ad14_defaults_everywhere() -> None:
    table = HealthThresholdTable()
    for environment in ENVIRONMENTS:
        assert table.for_environment(environment) == DEFAULTS


def test_an_environment_override_applies_only_to_that_environment() -> None:
    table = HealthThresholdTable.model_validate({"production": {"stale_after_seconds": 120}})
    assert table.for_environment("production").stale_after_seconds == 120
    assert table.for_environment("staging").stale_after_seconds == 300
    # The rest of production's lines are inherited, not zeroed.
    assert table.for_environment("production").warning_after_seconds == 30
    assert table.for_environment("production").unreachable_after_seconds == 900


def test_default_then_environment_precedence() -> None:
    table = HealthThresholdTable.model_validate(
        {
            "default": {"stale_after_seconds": 200, "unreachable_after_seconds": 600},
            "production": {"stale_after_seconds": 100},
        }
    )
    assert table.for_environment("staging") == HealthThresholds(
        warning_after_seconds=30, stale_after_seconds=200, unreachable_after_seconds=600
    )
    assert table.for_environment("production") == HealthThresholds(
        warning_after_seconds=30, stale_after_seconds=100, unreachable_after_seconds=600
    )


def test_the_same_age_classifies_differently_per_environment() -> None:
    table = HealthThresholdTable.model_validate({"production": {"stale_after_seconds": 120}})
    age = seen(200)
    assert classify_health(table.for_environment("production"), age, NOW).state == "stale"
    assert classify_health(table.for_environment("staging"), age, NOW).state == "warning"


def test_an_override_that_breaks_the_order_is_refused_at_load() -> None:
    """Ordering is checked on the *merged* result, which is what runs."""
    with pytest.raises(ValidationError):
        HealthThresholdTable.model_validate({"production": {"stale_after_seconds": 1_000}})


def test_an_unknown_environment_key_is_refused_not_ignored() -> None:
    """A typo'd key would otherwise look set and do nothing."""
    with pytest.raises(ValidationError):
        HealthThresholdTable.model_validate({"prod": {"stale_after_seconds": 120}})


def test_an_unknown_threshold_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        HealthThresholdTable.model_validate({"default": {"stale_after": 120}})


def test_an_unknown_environment_at_lookup_falls_back_to_the_default() -> None:
    table = HealthThresholdTable.model_validate({"default": {"stale_after_seconds": 200}})
    assert table.for_environment("mars").stale_after_seconds == 200


def test_the_table_names_exactly_the_database_environments() -> None:
    """The table's keys and the ``servers.environment`` CHECK are one closed set."""
    assert set(HealthThresholdTable.model_fields) - {"default"} == set(ENVIRONMENTS)


# --- the setting ----------------------------------------------------------------


def test_the_setting_defaults_to_ad14(settings: Settings) -> None:
    assert settings.health_thresholds.for_environment("production") == DEFAULTS


def test_the_setting_reads_json_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DCP_HEALTH_THRESHOLDS",
        '{"default": {"warning_after_seconds": 45}, "production": {"stale_after_seconds": 200}}',
    )
    loaded = load_settings().health_thresholds
    assert loaded.for_environment("production") == HealthThresholds(
        warning_after_seconds=45, stale_after_seconds=200, unreachable_after_seconds=900
    )
    assert loaded.for_environment("other").warning_after_seconds == 45


@pytest.mark.parametrize("empty", ["", "  "])
def test_an_empty_setting_is_the_defaults(monkeypatch: pytest.MonkeyPatch, empty: str) -> None:
    """Compose passes an unset variable through as the empty string."""
    monkeypatch.setenv("DCP_HEALTH_THRESHOLDS", empty)
    assert load_settings().health_thresholds.for_environment("production") == DEFAULTS


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[]",
        '{"production": {"stale_after_seconds": 10}}',  # below warning (30)
        '{"nowhere": {}}',
    ],
)
def test_a_bad_setting_fails_startup_legibly(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("DCP_HEALTH_THRESHOLDS", bad)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "DCP_HEALTH_THRESHOLDS" in str(caught.value) or "health_thresholds" in str(caught.value)


def test_the_old_global_setting_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Left in place, ``DCP_SERVER_STALE_AFTER_SECONDS`` would look set and do nothing."""
    monkeypatch.setenv("DCP_SERVER_STALE_AFTER_SECONDS", "300")
    with pytest.raises(ConfigurationError):
        load_settings()
