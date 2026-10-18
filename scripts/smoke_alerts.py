"""Assertions for scripts/smoke-test.sh step 18g (M3.3): alerting against the REAL pipeline.

Run INSIDE the ``sample-logs`` container (``exec -T -e SMOKE_COOKIE=... sample-logs python -
MODE``), so it reaches the backend, Prometheus, Alertmanager and Redis over the project
network. The shell stops and starts things between modes. Modes:

  pipeline   the watchdog has travelled Prometheus -> Alertmanager -> webhook, DCP says so,
             forged calls are refused AND counted, and nothing is stored for them
  down       the exporter is stopped: a REAL alert row appears for the right server, a repeat
             is counted not duplicated, and acknowledging it does not resolve it
  recovered  the exporter is back: the source resolves the alert and the history is kept
  outage     Alertmanager is stopped: the integration page says so, with a reason code
  restored   Alertmanager is back: the page is ``ok`` again

Why this exists beside the integration suite: that suite posts payloads *shaped like* the real
one (captured from a real Alertmanager in the M3.3 spike). This runs the rule file DCP
generated, through a real Prometheus and a real Alertmanager, into the webhook -- so it finds
what a stand-in cannot: a rule that does not load, a route that does not reach DCP, a secret
file the wrong user cannot read.

One honest exception: Alertmanager re-notifies a firing alert only every ``repeat_interval``
(1 hour), so a *repeat* cannot be waited for. The repeat is therefore posted by this script to
the real endpoint with the real secret, with the labels the real alert stored. That proves the
endpoint's deduplication, not Alertmanager's scheduling, and is labelled as such below.

Every "nothing was stored" is paired with a counter: an absence alone is also what a dead
endpoint looks like.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import redis

API = "http://backend:8000/api/v1"
ORIGIN = "http://backend:8000"
PROMETHEUS = "http://prometheus:9090"
ALERTMANAGER = "http://alertmanager:9093"
WEBHOOK = f"{API}/webhooks/alerts"
EXPORTER_NAME = "Smoke exporter"
SECRET = os.environ["SMOKE_WEBHOOK_SECRET"]
WAIT = 300


def say(text: str) -> None:
    sys.stdout.write(f"  {text}\n")


def call(method: str, path: str, body: dict | None = None, *, base: str = API, headers=None):
    all_headers = {
        "Cookie": os.environ["SMOKE_COOKIE"],
        "Origin": ORIGIN,
        "X-CSRF-Token": os.environ["SMOKE_CSRF"],
        "Content-Type": "application/json",
        **(headers or {}),
    }
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read().decode()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def webhook(payload: dict, secret: str | None) -> tuple[int, object]:
    """The real endpoint, with no session at all -- as Alertmanager calls it."""
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(
        WEBHOOK, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode() or "{}")


def wait_for(description: str, check, *, timeout: int = WAIT, every: int = 5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = check()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout}s waiting for: {description} (last: {last!r})")


# --- what the SPA would read ------------------------------------------------------------


def exporter_server() -> dict:
    status, body = call("GET", "/servers?" + urllib.parse.urlencode({"q": EXPORTER_NAME}))
    assert status == 200, body
    [server] = [item for item in body["items"] if item["display_name"] == EXPORTER_NAME]
    return server


def alerts_for(server_id: str, *states: str) -> list[dict]:
    params = [("target_id", server_id), ("limit", "100")]
    params += [("status", s) for s in (states or ("FIRING", "ACKNOWLEDGED", "RESOLVED"))]
    status, body = call("GET", "/alerts?" + urllib.parse.urlencode(params))
    assert status == 200, body
    return body["items"]


def not_reporting(server_id: str, *states: str) -> list[dict]:
    return [a for a in alerts_for(server_id, *states) if a["rule_name"] == "HostNotReporting"]


def alertmanager_row() -> dict:
    status, body = call("GET", "/integrations/status")
    assert status == 200, body
    return next(r for r in body["integrations"] if r["name"] == "Alertmanager")


def refused(reason: str) -> int:
    client = redis.Redis.from_url("redis://redis:6379/0", decode_responses=True)
    day = datetime.now(UTC).strftime("%Y%m%d")
    return int(client.get(f"dcp:alerts:refused:{reason}:{day}") or 0)


# --- modes ---------------------------------------------------------------------------------


def pipeline() -> None:
    server = exporter_server()
    sid = server["id"]
    assert server["environment"] == "staging"

    # The rule file DCP generated was loaded by a real Prometheus.
    with urllib.request.urlopen(f"{PROMETHEUS}/api/v1/rules", timeout=20) as response:
        groups = {g["name"]: g for g in json.load(response)["data"]["groups"]}
    assert {"dcp-watchdog", "dcp-staging", "dcp-production"} <= set(groups), sorted(groups)
    staging = {r["name"] for r in groups["dcp-staging"]["rules"]}
    assert {"HostNotReporting", "HostCpuWarning", "HostCpuCritical", "SystemdUnitFailed"} <= staging
    assert all(r["health"] == "ok" for g in groups.values() for r in g["rules"]), "a rule failed"
    say(f"Prometheus loaded {sum(len(g['rules']) for g in groups.values())} generated rules")

    # The watchdog travels the whole way and DCP can say so: this is the proof that alerts flow.
    row = wait_for(
        "the Alertmanager row to be ok (the watchdog reached the webhook)",
        lambda: (r := alertmanager_row())["status"] == "ok" and r,
    )
    assert row["last_success_at"], row
    status, summary = call("GET", "/dashboard/summary")
    assert status == 200 and summary["active_alerts"]["pipeline"] == "confirmed", summary
    say(f"watchdog arrived through the real chain at {row['last_success_at']}")

    # ... and it is only a heartbeat: no alert row exists for it, anywhere.
    status, body = call("GET", "/alerts?" + urllib.parse.urlencode(
        [("q", "Always firing"), ("status", "FIRING"), ("status", "ACKNOWLEDGED"),
         ("status", "RESOLVED"), ("status", "EXPIRED")]))
    assert status == 200 and body["items"] == [], "the watchdog was stored as an alert"

    # Nothing is wrong with the exporter yet, so no stopped-reporting alert is open.
    assert not_reporting(sid, "FIRING", "ACKNOWLEDGED") == []

    # Forged calls: refused, counted, and nothing stored.
    forged = {
        "version": "4", "status": "firing", "receiver": "dcp", "groupKey": "g", "groupLabels": {},
        "commonLabels": {}, "commonAnnotations": {}, "externalURL": "http://x",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "HostNotReporting", "server_id": sid, "instance": "forged:9100"},
            "annotations": {"summary": "FORGED"}, "startsAt": "2026-01-01T00:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z", "generatorURL": "", "fingerprint": "f",
        }],
    }
    before = refused("bad_secret")
    assert webhook(forged, None)[0] == 401
    assert webhook(forged, "not-the-secret-" * 4)[0] == 401
    assert refused("bad_secret") == before + 2, "the refusals were not counted"
    assert not_reporting(sid, "FIRING", "ACKNOWLEDGED") == [], "a forged alert was stored"
    say("a missing and a wrong secret: 401 each, counted, nothing stored")


def down() -> None:
    sid = exporter_server()["id"]
    found = wait_for(
        "a HostNotReporting alert for the stopped exporter",
        lambda: not_reporting(sid, "FIRING"),
    )
    assert len(found) == 1, found
    item = found[0]
    assert item["source"] == "PROMETHEUS" and item["severity"] == "CRITICAL"
    assert item["environment"] == "staging"
    assert item["target"] == {"type": "server", "id": sid, "display_name": EXPORTER_NAME}
    assert item["links"]["server"] == f"/api/v1/servers/{sid}"
    assert item["resolved_at"] is None
    say(f"a real alert arrived: {item['summary']!r} (occurrence_count={item['occurrence_count']})")

    # The repeat: posted here, to the real endpoint with the real secret (see the docstring).
    status, detail = call("GET", f"/alerts/{item['alert_id']}")
    assert status == 200, detail
    labels = detail["details"]["labels"]
    payload = {
        "version": "4", "status": "firing", "receiver": "dcp", "groupKey": "g", "groupLabels": {},
        "commonLabels": {}, "commonAnnotations": {}, "externalURL": "http://x",
        "alerts": [{
            "status": "firing", "labels": labels, "annotations": detail["details"]["annotations"],
            "startsAt": detail["details"]["starts_at"], "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "", "fingerprint": detail["details"]["fingerprint"],
        }],
    }
    before = item["occurrence_count"]
    code, body = webhook(payload, SECRET)
    assert code == 202 and body["results"][0]["action"] == "updated", body
    again = not_reporting(sid, "FIRING")
    assert len(again) == 1 and again[0]["occurrence_count"] == before + 1, again
    assert again[0]["alert_id"] == item["alert_id"]
    say("a repeat was counted on the same row, not inserted as a second one")

    # Acknowledging says "someone has it" and nothing else.
    status, acked = call("POST", f"/alerts/{item['alert_id']}/acknowledge", {"note": "smoke"})
    assert status == 200, acked
    assert acked["status"] == "ACKNOWLEDGED" and acked["resolved_at"] is None
    status, summary = call("GET", "/dashboard/summary")
    assert summary["active_alerts"]["critical"] >= 1, summary["active_alerts"]
    status, server = call("GET", f"/servers/{sid}")
    assert server["open_alerts"]["counts"]["critical"] >= 1, server["open_alerts"]
    still = not_reporting(sid, "ACKNOWLEDGED")
    assert len(still) == 1 and still[0]["resolved_at"] is None
    say("acknowledged: still open, resolved_at null, visible on the dashboard and server page")


def recovered() -> None:
    sid = exporter_server()["id"]
    resolved = wait_for(
        "the source to resolve the alert once the exporter is back",
        lambda: not_reporting(sid, "RESOLVED"),
    )
    item = resolved[0]
    assert item["resolved_at"] and item["acknowledged_by"], item
    status, detail = call("GET", f"/alerts/{item['alert_id']}")
    events = [h["event"] for h in detail["history"]]
    assert events[0] == "first_seen" and "acknowledged" in events and events[-1] == "resolved", events
    assert not_reporting(sid, "FIRING", "ACKNOWLEDGED") == []
    status, server = call("GET", f"/servers/{sid}")
    assert server["open_alerts"]["counts"]["critical"] == 0, server["open_alerts"]
    say("resolved by the source; who acknowledged it and the timeline are kept")


def outage() -> None:
    row = wait_for(
        "the Alertmanager row to report the outage",
        lambda: (r := alertmanager_row())["status"] == "unavailable" and r,
        timeout=60,
        every=3,
    )
    # A stopped container on the Docker network usually shows as a timeout (the name stops
    # resolving or the connect hangs), a refused connection as unreachable: both are the
    # outage, named by a closed reason code. Found by the first real run, which expected only
    # one of them.
    assert row["last_error_message"] in {"alertmanager_unreachable", "alertmanager_timeout"}, row
    assert "://" not in json.dumps(row), "the row leaks an address"
    say("Alertmanager stopped: the page says unavailable, with a reason code")


def restored() -> None:
    wait_for(
        "the Alertmanager row to be ok again",
        lambda: (r := alertmanager_row())["status"] == "ok" and r,
        timeout=120,
        every=3,
    )
    say("Alertmanager back: the page is ok again")


if __name__ == "__main__":
    mode = sys.argv[1]
    {"pipeline": pipeline, "down": down, "recovered": recovered, "outage": outage,
     "restored": restored}[mode]()
    say(f"mode {mode}: PASSED")
