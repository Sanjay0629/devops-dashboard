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


# --- notifications (step 18h, M3.4) ----------------------------------------------------------
#
# These ride on 18g's REAL alert (the stopped exporter): a channel is registered before the
# exporter stops, so the alert Prometheus and Alertmanager really produce is the one that is
# notified. The destination is the ``notification-sink`` mock, which is NOT Slack, Teams or a
# mail server: what this proves is DCP's own behaviour -- what it sends, to where, how it
# retries -- not that a provider accepts the message.

SINK = "http://notification-sink:8080"
WEBHOOK_CHANNEL = "smoke-webhook"
EMAIL_CHANNEL = "smoke-email"
CANARY_HOST = "notification-sink"


def sink(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(SINK + path, data=data, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode()
    return json.loads(raw) if raw and raw.lstrip().startswith("{") else raw


def sink_mode(mode: str) -> None:
    sink("POST", "/__mode", {"mode": mode})


def sink_received() -> list[dict]:
    return sink("GET", "/__received")["received"]


def mine(alert_id: str) -> list[dict]:
    """What the sink received about this alert (other alerts may notify it too)."""
    return [
        r for r in sink_received()
        if (r["kind"] == "http" and (r["body"] or {}).get("alert_id") == alert_id)
        or (r["kind"] == "smtp" and alert_id in r["data"])
    ]


def channel_rows() -> dict[str, dict]:
    status, body = call("GET", "/integrations/status")
    assert status == 200, body
    return {r["name"]: r for r in body["integrations"] if r["name"].startswith("Notifications:")}


def the_alert(sid: str) -> dict:
    found = wait_for(
        "the stopped-reporting alert",
        lambda: not_reporting(sid, "FIRING", "ACKNOWLEDGED", "RESOLVED"),
    )
    # Step 18a stopped the same exporter earlier, so an older, resolved alert exists too:
    # the one notified is the newest.
    return max(found, key=lambda a: a["first_seen_at"])


def deliveries(alert_id: str) -> dict[str, dict]:
    status, detail = call("GET", f"/alerts/{alert_id}")
    assert status == 200, detail
    return {d["channel_name"]: d for d in detail["notifications"]}


def notify_register() -> None:
    sink("DELETE", "/__received")
    sink_mode("unavailable")  # the first thing a delivery meets is a failing destination
    for body in (
        {"name": WEBHOOK_CHANNEL, "channel_type": "WEBHOOK",
         "destination_reference": "env://NOTIFY_SINK",
         "routing_rules": {"severity": ["CRITICAL"], "environment": ["staging"]}},
        {"name": EMAIL_CHANNEL, "channel_type": "EMAIL",
         "destination_reference": "ops@example.test"},
    ):
        status, created = call("POST", "/notification-channels", body)
        assert status == 201, created
        # The reference comes back as registered; the resolved URL never does.
        assert CANARY_HOST not in json.dumps(created) or body["channel_type"] == "EMAIL", created
        assert "http://" not in json.dumps(created), created
    status, listed = call("GET", "/notification-channels")
    assert status == 200 and listed["email_available"] is True, listed
    assert "http://" not in json.dumps(listed), "a destination URL was returned by the API"
    status, refused_url = call("POST", "/notification-channels", {
        "name": "a-url", "channel_type": "SLACK", "destination_reference": "http://x.invalid/hook"})
    assert status == 422 and "x.invalid" not in json.dumps(refused_url), refused_url
    rows = wait_for("both channels to appear on the integration page",
                    lambda: len(r := channel_rows()) == 2 and r)
    for name in (f"Notifications: {WEBHOOK_CHANNEL}", f"Notifications: {EMAIL_CHANNEL}"):
        assert rows[name]["status"] == "degraded", rows[name]
        assert rows[name]["last_error_message"] == "no_delivery_attempted_yet", rows[name]
    status, alerts = call("GET", "/alerts?limit=1")
    assert alerts["coverage"]["notification_channels_active"] == 2, alerts["coverage"]
    say("two channels registered (a webhook by reference, an address); a URL is refused unechoed; "
        "both are 'degraded: never attempted', not 'ok'")


def notify_failures() -> None:
    sid = exporter_server()["id"]
    alert = the_alert(sid)
    try:
        got = wait_for(
            "both deliveries to exhaust their retries against a failing destination",
            lambda: (d := deliveries(alert["alert_id"])).keys() >= {WEBHOOK_CHANNEL, EMAIL_CHANNEL}
            and all(v["status"] == "ABANDONED" for v in d.values()) and d,
            timeout=400,
        )
    except AssertionError:
        # Say what the deliveries ARE: a wait that only reports "False" hid a permanent
        # failure (an SMTP 4xx read as a refusal) for a whole run.
        for name, d in deliveries(alert["alert_id"]).items():
            say(f"{name}: {d['status']} attempts={d['attempt_count']} last_error={d['last_error']}")
        raise
    hook, mail = got[WEBHOOK_CHANNEL], got[EMAIL_CHANNEL]
    # Delays 5,5,5 are the smoke run's setting: four attempts, then abandoned.
    assert (hook["attempt_count"], hook["last_error"]) == (4, "http_503"), hook
    assert (mail["attempt_count"], mail["last_error"]) == (4, "smtp_busy"), mail
    assert hook["retryable"] and mail["retryable"] and hook["next_attempt_at"] is None
    posts = [r for r in mine(alert["alert_id"]) if r["kind"] == "http"]
    assert len(posts) == 4, f"expected four attempts at the destination, saw {len(posts)}"
    body = posts[0]["body"]
    assert body["schema"] == "dcp.alert.v1" and body["severity"] == "CRITICAL", body
    assert body["alert_id"] == alert["alert_id"] and body["environment"] == "staging", body
    assert body["link"] == f"{os.environ['SMOKE_ORIGIN']}/alerts/{alert['alert_id']}", body
    assert posts[0]["host"] == CANARY_HOST + ":8080", posts[0]["host"]
    rows = channel_rows()
    for name in (f"Notifications: {WEBHOOK_CHANNEL}", f"Notifications: {EMAIL_CHANNEL}"):
        assert rows[name]["status"] == "unavailable", rows[name]
    assert rows[f"Notifications: {WEBHOOK_CHANNEL}"]["last_error_message"] == "http_503"
    assert "http://" not in json.dumps(rows) and CANARY_HOST not in json.dumps(rows)
    say("a failing destination: 4 attempts each, then ABANDONED with a reason code; the page says "
        "unavailable and names no address")


def retry(alert_id: str, delivery_id: str) -> tuple[int, object]:
    return call("POST", f"/alerts/{alert_id}/notifications/{delivery_id}/retry")


def notify_retry() -> None:
    sid = exporter_server()["id"]
    alert = the_alert(sid)
    aid = alert["alert_id"]
    before = len(mine(aid))
    # A permanent failure: the destination answers with a redirect. Not followed, not retried.
    sink_mode("redirect")
    hook = deliveries(aid)[WEBHOOK_CHANNEL]
    status, body = retry(aid, hook["delivery_id"])
    assert status == 202 and body["status"] == "PENDING" and body["attempt_count"] == 0, body
    failed = wait_for(
        "the redirecting destination to be a permanent failure",
        lambda: (d := deliveries(aid)[WEBHOOK_CHANNEL])["status"] == "FAILED" and d,
        timeout=120,
    )
    assert (failed["attempt_count"], failed["last_error"]) == (1, "redirect_refused"), failed
    time.sleep(35)  # more than two dispatch ticks: a permanent failure is not retried
    assert len(mine(aid)) == before + 1, "a redirect was retried or followed"
    say("a redirect is refused as a permanent failure, attempted exactly once")

    sink_mode("ok")
    got = deliveries(aid)
    for name in (WEBHOOK_CHANNEL, EMAIL_CHANNEL):
        status, again = retry(aid, got[name]["delivery_id"])
        assert status == 202, again
    done = wait_for(
        "both retried deliveries to be sent",
        lambda: (d := deliveries(aid)) and all(v["status"] == "SENT" for v in d.values()) and d,
        timeout=120,
    )
    assert all(v["last_error"] is None and v["sent_at"] and not v["retryable"] for v in done.values())
    received = mine(aid)
    ok_posts = [r for r in received if r["kind"] == "http" and r["mode"] == "ok"]
    mails = [r for r in received if r["kind"] == "smtp"]
    assert len(ok_posts) == 1 and len(mails) == 1, (len(ok_posts), len(mails))
    assert ok_posts[0]["body"]["alert_id"] == aid
    assert mails[0]["to"] == ["<ops@example.test>"] and mails[0]["from"] == "<dcp@example.test>"
    assert "Subject: [DCP] [CRITICAL] HostNotReporting (staging)" in mails[0]["data"], mails[0]
    assert aid in mails[0]["data"]
    # A delivered message is never re-sent by a button.
    status, body = retry(aid, done[WEBHOOK_CHANNEL]["delivery_id"])
    assert status == 409 and body["error"]["code"] == "invalid_state_transition", body
    rows = channel_rows()
    assert all(r["status"] == "ok" for r in rows.values()), rows
    say("manual retry from FAILED and from ABANDONED: both SENT, one message each, the page is ok; "
        "retrying a SENT delivery is a 409")


def notify_quiet() -> None:
    sid = exporter_server()["id"]
    alert = the_alert(sid)
    assert alert["status"] == "RESOLVED", alert
    before = mine(alert["alert_id"])
    time.sleep(50)  # three dispatch ticks
    after = mine(alert["alert_id"])
    assert len(after) == len(before), "a resolve, a repeat or an expiry sent a message (OPEN-51)"
    assert all(v["status"] == "SENT" for v in deliveries(alert["alert_id"]).values())
    say("the alert resolved and nothing more was sent: a channel hears that a problem began")


if __name__ == "__main__":
    mode = sys.argv[1]
    {"pipeline": pipeline, "down": down, "recovered": recovered, "outage": outage,
     "restored": restored, "notify_register": notify_register,
     "notify_failures": notify_failures, "notify_retry": notify_retry,
     "notify_quiet": notify_quiet}[mode]()
    say(f"mode {mode}: PASSED")
