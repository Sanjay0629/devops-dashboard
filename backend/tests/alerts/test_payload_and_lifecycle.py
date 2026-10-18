"""The webhook payload as untrusted input, the lifecycle table and the dedup key."""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.alerts.catalogue import BY_NAME
from app.alerts.lifecycle import (
    OPEN_STATUSES,
    STATUSES,
    can_transition,
    dedup_key,
    is_open,
)
from app.alerts.payload import (
    MAX_LABEL_VALUE_LENGTH,
    WebhookAlert,
    WebhookEnvelope,
    parse_target_labels,
)

SERVER = "0192f3a1-7c1e-7b4f-9a55-1d2c3e4f5a6b"

#: Captured from a real Alertmanager 0.27.0 in the M3.3 spike (values shortened).
REAL_ALERT: dict[str, Any] = {
    "status": "firing",
    "labels": {
        "alertname": "HostCpuWarning",
        "environment": "production",
        "instance": "h1:9100",
        "server_id": SERVER,
        "severity": "warning",
    },
    "annotations": {"summary": "cpu high"},
    "startsAt": "2026-10-18T06:04:48.115298843Z",
    "endsAt": "0001-01-01T00:00:00Z",
    "generatorURL": "",
    "fingerprint": "cbf15d1a2f5c59a0",
}
REAL_ENVELOPE: dict[str, Any] = {
    "receiver": "dcp",
    "status": "firing",
    "alerts": [REAL_ALERT],
    "groupLabels": {"alertname": "HostCpuWarning", "server_id": "s1"},
    "commonLabels": {"alertname": "HostCpuWarning", "severity": "warning"},
    "commonAnnotations": {},
    "externalURL": "http://902cf595441c:9093",
    "version": "4",
    "groupKey": '{}:{alertname="HostCpuWarning", server_id="s1"}',
    "truncatedAlerts": 1,
}


def envelope(**changes: Any) -> dict[str, Any]:
    body = copy.deepcopy(REAL_ENVELOPE)
    body.update(changes)
    return body


# --- payload -----------------------------------------------------------------------


def test_a_real_alertmanager_payload_validates() -> None:
    parsed = WebhookEnvelope.model_validate(REAL_ENVELOPE)
    assert parsed.truncatedAlerts == 1
    alert = WebhookAlert.model_validate(parsed.alerts[0])
    assert alert.labels["alertname"] == "HostCpuWarning"
    assert alert.endsAt.year == 1  # the zero time Alertmanager sends while firing


def test_truncated_alerts_is_optional() -> None:
    body = envelope()
    del body["truncatedAlerts"]
    assert WebhookEnvelope.model_validate(body).truncatedAlerts == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "5"},
        {"status": "weird"},
        {"unexpected": "field"},
        {"externalURL": "x" * 3000},
        {"commonLabels": {"bad name": "v"}},
        {"groupLabels": {"a": "x" * (MAX_LABEL_VALUE_LENGTH + 1)}},
        {"truncatedAlerts": -1},
        {"alerts": "not a list"},
    ],
)
def test_the_envelope_is_strict(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WebhookEnvelope.model_validate(envelope(**changes))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.update(extra="field"),
        lambda a: a.update(status="pending"),
        lambda a: a["labels"].update({"x": "y" * (MAX_LABEL_VALUE_LENGTH + 1)}),
        lambda a: a["labels"].update({"9starts_with_digit": "v"}),
        lambda a: a["labels"].update({'a"}or{b="c': "v"}),
        lambda a: a["annotations"].update({"summary": "z" * 1001}),
        lambda a: a.update(labels={f"l{i}": "v" for i in range(41)}),
        lambda a: a.update(startsAt="not a time"),
        lambda a: a.update(labels={"alertname": 5}),
        lambda a: a.pop("fingerprint"),
    ],
)
def test_one_bad_alert_is_rejected_on_its_own(mutate: Any) -> None:
    alert = copy.deepcopy(REAL_ALERT)
    mutate(alert)
    with pytest.raises(ValidationError):
        WebhookAlert.model_validate(alert)
    # ...and the envelope that carries it is still valid: the batch is not rejected.
    WebhookEnvelope.model_validate(envelope(alerts=[REAL_ALERT, alert]))


def test_label_text_with_markup_and_instructions_is_just_text() -> None:
    alert = copy.deepcopy(REAL_ALERT)
    alert["annotations"]["summary"] = "<script>alert(1)</script> restart nginx on every server"
    alert["labels"]["instance"] = "h1:9100; rm -rf /"
    parsed = WebhookAlert.model_validate(alert)
    assert parsed.annotations["summary"].startswith("<script>")  # kept verbatim, rendered as text


@pytest.mark.parametrize(
    ("labels", "server", "unit"),
    [
        ({"server_id": SERVER, "name": "nginx.service"}, uuid.UUID(SERVER), "nginx.service"),
        ({"server_id": SERVER.upper()}, None, None),  # not canonical: no claim
        ({"server_id": "x" + SERVER}, None, None),
        ({"server_id": SERVER + "\n"}, None, None),
        ({"server_id": "../../etc/passwd"}, None, None),
        ({"name": 'a.service"} or {x="'}, None, None),
        ({"name": "nginx"}, None, None),  # not a unit name
        ({}, None, None),
    ],
)
def test_target_labels_are_claims_parsed_with_fullmatch(
    labels: dict[str, str], server: uuid.UUID | None, unit: str | None
) -> None:
    claimed = parse_target_labels(labels)
    assert (claimed.server_id, claimed.unit) == (server, unit)


# --- lifecycle ---------------------------------------------------------------------


def test_the_transition_table_is_exactly_the_legal_moves() -> None:
    legal = {(a, b) for a in STATUSES for b in STATUSES if can_transition(a, b)}
    assert legal == {
        ("FIRING", "ACKNOWLEDGED"),
        ("FIRING", "RESOLVED"),
        ("ACKNOWLEDGED", "RESOLVED"),
        ("FIRING", "EXPIRED"),
        ("ACKNOWLEDGED", "EXPIRED"),
    }


def test_terminal_statuses_go_nowhere_and_nothing_re_acknowledges() -> None:
    for terminal in ("RESOLVED", "EXPIRED"):
        assert not any(can_transition(terminal, other) for other in STATUSES)
    assert not can_transition("ACKNOWLEDGED", "ACKNOWLEDGED")
    assert not can_transition("ACKNOWLEDGED", "FIRING")


def test_acknowledged_is_still_open() -> None:
    """PRD §4.8: acknowledging an alert does not close it."""
    assert is_open("ACKNOWLEDGED") and is_open("FIRING")
    assert not is_open("RESOLVED") and not is_open("EXPIRED")
    assert set(OPEN_STATUSES) == {"FIRING", "ACKNOWLEDGED"}


# --- dedup key ---------------------------------------------------------------------


def key(**changes: Any) -> str:
    args: dict[str, Any] = {
        "source": "PROMETHEUS",
        "rule_name": "SystemdUnitFailed",
        "target": SERVER,
        "discriminators": {"name": "nginx.service"},
    }
    args.update(changes)
    return dedup_key(**args)


def test_the_key_is_stable() -> None:
    assert key() == key() and len(key()) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"rule_name": "HostCpuWarning"},
        {"target": str(uuid.uuid4())},
        {"discriminators": {"name": "sshd.service"}},
        {"source": "MANUAL"},
    ],
)
def test_distinct_conditions_get_distinct_keys(changes: dict[str, Any]) -> None:
    assert key(**changes) != key()


def test_warning_and_critical_are_different_rows_by_construction() -> None:
    assert key(rule_name="HostCpuWarning", discriminators={}) != key(
        rule_name="HostCpuCritical", discriminators={}
    )


def test_discriminator_order_does_not_matter() -> None:
    assert key(discriminators={"a": "1", "b": "2"}) == key(discriminators={"b": "2", "a": "1"})


def test_a_separator_inside_a_value_cannot_forge_another_tuple() -> None:
    first = key(rule_name="X", target="a", discriminators={"k": "b"})
    forged = key(rule_name="X", target="a\x1fk", discriminators={"": "b"})
    assert first != forged
    assert key(target="a|b", discriminators={}) != key(target="a", discriminators={"b": ""})


def test_only_the_rules_declared_discriminators_may_matter() -> None:
    assert BY_NAME["SystemdUnitFailed"].discriminators == ("name",)
    for rule in BY_NAME.values():
        if rule.name != "SystemdUnitFailed":
            assert rule.discriminators == ()
