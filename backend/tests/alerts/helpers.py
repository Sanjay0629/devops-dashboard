"""Builders for Alertmanager payloads, shaped like the real one captured in the M3.3 spike."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SECRET = "test-alert-webhook-secret-0123456789abcdef"
WEBHOOK = "/api/v1/webhooks/alerts"


def alert(
    name: str,
    *,
    status: str = "firing",
    server_id: str | None = None,
    summary: str = "something is wrong",
    instance: str = "h1:9100",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_labels = {"alertname": name, "instance": instance, "job": "node"}
    if server_id is not None:
        base_labels["server_id"] = server_id
    base_labels.update(labels or {})
    return {
        "status": status,
        "labels": base_labels,
        "annotations": {"summary": summary, **(annotations or {})},
        "startsAt": now,
        "endsAt": "0001-01-01T00:00:00Z" if status == "firing" else now,
        "generatorURL": "http://prometheus:9090/graph?g0.expr=up",
        "fingerprint": "cbf15d1a2f5c59a0",
    }


def envelope(*alerts: dict[str, Any], **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "receiver": "dcp",
        "status": "firing",
        "alerts": list(alerts),
        "groupLabels": {"alertname": "x"},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "version": "4",
        "groupKey": '{}:{alertname="x"}',
    }
    body.update(changes)
    return body


def bearer(secret: str = SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}
