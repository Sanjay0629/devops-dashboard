"""The utilisation table, the catalogue and the generated Prometheus rule file (OPEN-46)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template

import pytest
from pydantic import ValidationError

from app.alerts import rulegen
from app.alerts.catalogue import (
    BY_NAME,
    NOT_PRODUCED_YET,
    OPERATION_RULE,
    RULES,
    UNIT_FAILED_FOR_SECONDS,
    WATCHDOG_RULE,
)
from app.approvals.models import ENVIRONMENTS
from app.core.config import ConfigurationError, load_settings
from app.metrics.templates import BY_ID, CATALOGUE
from app.monitoring.health import HealthThresholdTable
from app.monitoring.utilisation import (
    THRESHOLD_METRICS,
    UtilisationThresholdTable,
    catalogue_defaults,
)

RULE_FILE = Path(__file__).resolve().parents[3] / "infra/prometheus/rules/dcp-alerts.yml"


# --- the table -----------------------------------------------------------------


def test_the_table_covers_exactly_the_templates_that_carry_thresholds() -> None:
    carrying = {t.id for t in CATALOGUE if t.default_thresholds is not None}
    assert set(THRESHOLD_METRICS) == carrying


def test_defaults_come_from_the_catalogue_and_are_not_a_second_copy() -> None:
    lines = UtilisationThresholdTable().for_environment("production")
    for metric in THRESHOLD_METRICS:
        catalogue = BY_ID[metric].default_thresholds
        assert catalogue is not None
        assert (lines[metric].warning, lines[metric].critical) == (
            catalogue["warning"],
            catalogue["critical"],
        )
    assert catalogue_defaults()["cpu_utilisation"] == {"warning": 80.0, "critical": 90.0}


def test_overrides_layer_default_then_environment_and_inherit_the_rest() -> None:
    table = UtilisationThresholdTable.model_validate(
        {
            "default": {"cpu_utilisation": {"warning": 70}},
            "production": {
                "cpu_utilisation": {"critical": 85},
                "disk_utilisation": {"warning": 80},
            },
        }
    )
    production = table.for_environment("production")
    assert (production["cpu_utilisation"].warning, production["cpu_utilisation"].critical) == (
        70,
        85,
    )
    assert production["disk_utilisation"].warning == 80
    assert production["disk_utilisation"].critical == 95  # inherited
    staging = table.for_environment("staging")
    assert (staging["cpu_utilisation"].warning, staging["cpu_utilisation"].critical) == (70, 90)
    # An unknown environment gets the default layer, not a failure.
    assert table.for_environment("nonsense")["cpu_utilisation"].warning == 70


@pytest.mark.parametrize(
    "raw",
    [
        {"production": {"cpu_utilisation": {"warning": 95}}},  # 95 >= inherited critical 90
        {"default": {"cpu_utilisation": {"warning": 90, "critical": 90}}},  # equal
        {"default": {"cpu_utilisation": {"critical": 101}}},  # out of range
        {"default": {"cpu_utilisation": {"warning": 0}}},
        {"prod": {}},  # a typo is a startup failure, not a setting that looks applied
        {"default": {"cpu_utilization": {"warning": 70}}},  # misspelt metric
        {"default": {"cpu_utilisation": {"warning": 70, "bogus": 1}}},
    ],
)
def test_a_bad_table_is_refused_at_load(raw: dict) -> None:
    with pytest.raises(ValidationError):
        UtilisationThresholdTable.model_validate(raw)


def test_the_keys_are_exactly_the_environment_set() -> None:
    fields = set(UtilisationThresholdTable.model_fields) - {"default"}
    assert fields == set(ENVIRONMENTS)


def test_the_setting_is_read_from_json_and_empty_means_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DCP_UTILISATION_THRESHOLDS", "")
    assert load_settings().utilisation_thresholds == UtilisationThresholdTable()
    monkeypatch.setenv(
        "DCP_UTILISATION_THRESHOLDS", '{"production": {"memory_utilisation": {"warning": 60}}}'
    )
    lines = load_settings().utilisation_thresholds.for_environment("production")
    assert lines["memory_utilisation"].warning == 60
    monkeypatch.setenv("DCP_UTILISATION_THRESHOLDS", "{not json")
    with pytest.raises(ConfigurationError):
        load_settings()
    monkeypatch.setenv(
        "DCP_UTILISATION_THRESHOLDS", '{"default": {"cpu_utilisation": {"warning": 99}}}'
    )
    with pytest.raises(ConfigurationError):  # 99 >= 90
        load_settings()


# --- the catalogue ---------------------------------------------------------------


def test_catalogue_names_are_unique_and_severity_agrees_with_the_name() -> None:
    assert len({r.name for r in RULES}) == len(RULES) == len(BY_NAME)
    for rule in RULES:
        if rule.name.endswith("Warning"):
            assert rule.severity == "WARNING"
        if rule.name.endswith("Critical"):
            assert rule.severity == "CRITICAL"
    assert WATCHDOG_RULE not in BY_NAME  # it is never stored, so it is not a row's rule


def test_every_condition_the_prd_lists_is_produced_or_named_as_not_produced() -> None:
    produced = {r.condition for r in RULES}
    assert produced == {"host_not_reporting", "cpu", "memory", "disk", "unit_failed"}
    # M4.3 gave the third its producer (``OPEN-89``): it moved out of this list, not into RULES,
    # because the webhook accepts only Prometheus conditions and must not be able to forge it.
    assert {name for name, _ in NOT_PRODUCED_YET} == {
        "application_health_check_failed",
        "deployment_failed",
    }
    assert OPERATION_RULE.condition == "operation_timed_out_or_failed"
    assert OPERATION_RULE.name not in BY_NAME


# --- the generated file -------------------------------------------------------------


def test_the_committed_rule_file_is_what_the_generator_produces() -> None:
    """The check `check-all.sh` also runs: a hand edit or a stale file fails here."""
    assert RULE_FILE.read_text(encoding="utf-8") == rulegen.render()


def test_rendering_is_deterministic() -> None:
    assert rulegen.render() == rulegen.render()


def test_every_environment_gets_every_rule_exactly_once() -> None:
    text = rulegen.render()
    for environment in ENVIRONMENTS:
        group = text.split(f"- name: dcp-{environment}\n", 1)[1].split("\n  - name:", 1)[0]
        names = re.findall(r"- alert: (\w+)", group)
        assert sorted(names) == sorted(r.name for r in RULES), environment
        # Every expression in the group is scoped to this environment.
        for expr in re.findall(r"expr: (.+)", group):
            assert f'environment=\\"{environment}\\"' in expr
    assert text.count(f"alert: {WATCHDOG_RULE}") == 1


def test_expressions_are_the_metric_catalogues_own() -> None:
    """The catalogue's text, with only $selector and $window filled in, is in the file."""
    text = rulegen.render()
    for template_id in THRESHOLD_METRICS:
        query = Template(BY_ID[template_id].queries["LINUX"]).substitute(
            selector='job="node",environment="production"', window="300s"
        )
        assert json.dumps(query)[1:-1] in text, template_id
    assert "$selector" not in text and "$window" not in text


def test_warning_excludes_critical_and_critical_does_not_reference_warning() -> None:
    text = rulegen.render()
    for prefix in ("HostCpu", "HostMemory", "HostDisk"):
        warning = re.search(rf"alert: {prefix}Warning\n\s+expr: (.+)", text)
        critical = re.search(rf"alert: {prefix}Critical\n\s+expr: (.+)", text)
        assert warning and critical
        assert ") unless (" in warning.group(1)
        assert "unless" not in critical.group(1)


def test_overrides_reach_the_file_and_the_chart_agrees() -> None:
    utilisation = UtilisationThresholdTable.model_validate(
        {"production": {"cpu_utilisation": {"warning": 70, "critical": 85}}}
    )
    text = rulegen.render(utilisation)
    production = text.split("- name: dcp-production\n", 1)[1].split("\n  - name:", 1)[0]
    assert ">= 70)" in production and ">= 85" in production and "(warning at 70%)" in production
    staging = text.split("- name: dcp-staging\n", 1)[1].split("\n  - name:", 1)[0]
    assert "(warning at 80%)" in staging  # another environment is untouched


def test_not_reporting_waits_for_the_environments_stale_line() -> None:
    health = HealthThresholdTable.model_validate(
        {
            "default": {
                "warning_after_seconds": 30,
                "stale_after_seconds": 45,
                "unreachable_after_seconds": 75,
            },
            "production": {"stale_after_seconds": 60},
        }
    )
    text = rulegen.render(health=health)
    for environment, expected in (("production", "60s"), ("staging", "45s")):
        group = text.split(f"- name: dcp-{environment}\n", 1)[1].split("\n  - name:", 1)[0]
        block = group.split("alert: HostNotReporting\n", 1)[1].split("annotations", 1)[0]
        assert f"for: {expected}" in block


def test_failed_unit_rule_selects_services_and_waits_a_minute() -> None:
    text = rulegen.render()
    block = text.split("alert: SystemdUnitFailed\n", 1)[1].split("annotations", 1)[0]
    assert 'state=\\"failed\\"' in block and "\\\\.service" in block
    assert f"for: {UNIT_FAILED_FOR_SECONDS}s" in block
