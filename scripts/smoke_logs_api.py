"""Assertions for scripts/smoke-test.sh step 18f (M3.2): the log API against a REAL Loki.

Run INSIDE the ``sample-logs`` container (``exec -T -e SMOKE_COOKIE=... sample-logs
python - MODE``), so it reaches the backend and Loki over the project network and
reads the very canary corpus the producer used. Modes:

  search     the whole query surface, against the real pipeline and the real Loki
  outage     Loki is stopped: the API says so and never returns an empty page
  recovered  Loki is back: the same query works again

Why this exists beside the integration suite: that suite's Loki is a stand-in that
evaluates the query with the semantics measured once. This runs the queries the
service really builds through a real Loki (does it accept them? do its ties page
without a repeat or a gap?), and it does so on lines that came through the real
Alloy and so have really been through both redaction layers.

Every "absent" assertion is paired with a "present" one for the same data: absence
on its own is what a dead pipeline, or a query that matched nothing, looks like.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

API = "http://backend:8000/api/v1"
# The CSRF check accepts the request's own origin without configuration (SECURITY.md §12),
# and these calls go straight to the backend rather than through the SPA's proxy.
ORIGIN = "http://backend:8000"
LOKI = "http://loki:3100"
# The sample log host (`python -m app.inventory.seed --sample-log-host`): the identity
# the sample Alloy ships as, registered under a *different* hostname than its label.
SERVER = "00000000-0000-4000-8000-00000000a110"
APPLICATION = "00000000-0000-4000-8000-0000000000a9"
INVENTORY_HOSTNAME = "sample-log-host.demo.invalid"
SHIPPER_HOSTNAME = "sample-host.internal.example"
MARKER = "[REDACTED]"
SOURCES = ["application", "docker", "nginx", "php_fpm", "syslog"]
LEVELS = {"debug", "info", "warn", "error", "critical", "unknown"}
SERVICE_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
CANARIES = json.load(open("/sample-logs/canaries.json", encoding="utf-8"))  # noqa: SIM115
SECRET_PLACEHOLDER = "<<SECRET>>"
# Unique per run, so a re-run against a kept stack does not see the previous run's lines.
RUN = uuid.uuid4().hex[:8]
RAW, HOSTILE, PRESETS = f"smoke-raw-{RUN}", f"smoke-hostile-{RUN}", f"smoke-presets-{RUN}"
TAG = f"PAGE-{RUN}-"


def say(text: str) -> None:
    sys.stdout.write(f"  {text}\n")


# --- the API, as the SPA calls it -----------------------------------------------


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    headers = {
        "Cookie": os.environ["SMOKE_COOKIE"],
        "Origin": ORIGIN,
        "X-CSRF-Token": os.environ["SMOKE_CSRF"],
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    for _ in range(4):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                status, raw = response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read().decode()
            if status == 429:
                # The limit (30 a minute, §19.1) is the product working. The paging check
                # alone makes more queries than that, so wait out the window as a client would.
                wait = min(int(error.headers.get("Retry-After") or 60), 65)
                say(f"rate limited; waiting {wait}s as Retry-After says")
                time.sleep(wait)
                continue
        break
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def window(minutes_back: int = 30) -> dict:
    now = datetime.now(UTC)
    return {
        "from": (now - timedelta(minutes=minutes_back)).isoformat(),
        "to": (now + timedelta(minutes=1)).isoformat(),
    }


def search(**body) -> dict:
    status, payload = call("POST", "/logs/query", {**window(), "server_ids": [SERVER], **body})
    assert status == 200, f"search {body} -> {status}: {str(payload)[:300]}"
    assert isinstance(payload, dict)
    return payload


def lines(payload: dict) -> list[str]:
    return [e["line"] for e in payload["entries"]]


def wait_for(description: str, probe, seconds: int = 150):
    deadline = time.time() + seconds
    while True:
        value = probe()
        if value:
            return value
        if time.time() > deadline:
            raise AssertionError(f"timed out after {seconds}s waiting for {description}")
        time.sleep(3)


# --- raw pushes, bypassing Alloy (the second redaction layer's whole point) -------


def push(streams: list[tuple[dict, list[tuple[int, str]]]]) -> None:
    body = json.dumps(
        {"streams": [{"stream": s, "values": [[str(t), line] for t, line in v]} for s, v in streams]}
    ).encode()
    request = urllib.request.Request(
        f"{LOKI}/loki/api/v1/push", body, {"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 204, response.status


def labels(service: str, **over: str) -> dict:
    return {
        "environment": "development",
        "server_id": SERVER,
        "hostname": SHIPPER_HOSTNAME,
        "application_id": APPLICATION,
        "service": service,
        "source_type": "application",
        "level": "info",
        **over,
    }


def now_ns() -> int:
    return time.time_ns()


# --- the checks --------------------------------------------------------------------


def catalogue() -> dict:
    status, sources = call("GET", "/logs/sources")
    assert status == 200 and isinstance(sources, dict), (status, sources)
    assert set(sources["source_types"]) == {*SOURCES, "journald"}, sources["source_types"]
    assert set(sources["levels"]) == LEVELS
    assert len(sources["presets"]) == 8
    with urllib.request.urlopen(f"{LOKI}/config", timeout=15) as response:
        config = response.read().decode()
    # OPEN-37: the backend's retention setting and Loki's own are one number.
    match = re.search(r"retention_period:\s*(\d+)([wd])", config)
    assert match, "Loki's retention_period is not in /config"
    days = int(match.group(1)) * (7 if match.group(2) == "w" else 1)
    assert sources["limits"]["retention_days"] == days, (sources["limits"], days)
    say(f"the catalogue is served; DCP_LOG_RETENTION_DAYS={days} equals Loki's retention_period")
    return sources


def real_pipeline() -> None:
    first = wait_for(
        "sample lines to reach the API for the registered sample server",
        lambda: (r := search(limit=50))["entries"] and r,
    )
    for entry in first["entries"]:
        assert entry["server_id"] == SERVER
        assert entry["hostname"] == INVENTORY_HOSTNAME, entry["hostname"]
        assert entry["level"] in LEVELS
        assert entry["service"] == "other" or SERVICE_SHAPE.fullmatch(entry["service"]), entry
        assert entry["timestamp"].endswith(("Z", "+00:00")), entry["timestamp"]
    say(f"{len(first['entries'])} entries: the inventory's hostname ({INVENTORY_HOSTNAME}), "
        f"never the shipper's label ({SHIPPER_HOSTNAME})")
    assert [i["state"] for i in first["ingestion"]] == ["receiving"], first["ingestion"]
    assert first["servers_searched"] == 1 and first["excluded_by_scope_count"] == 0
    say("the shipper is reported `receiving`")

    for source in SOURCES:
        got = wait_for(f"{source} lines", lambda s=source: (r := search(source_type=s, limit=20))["entries"] and r)
        assert {e["source_type"] for e in got["entries"]} == {source}
    say(f"each of the five file sources is searchable on its own: {SOURCES}")

    docker = search(source_type="docker", contains="Weird Name", limit=5)
    assert docker["entries"] and {e["service"] for e in docker["entries"]} == {"other"}
    say("a container named `Weird Name; DROP TABLE` is served as service=`other`")

    # Alloy already redacted these; the API must not undo that or add a leak.
    text = json.dumps(search(limit=1000))
    single = [c for c in CANARIES if not c.get("known_gap")]
    assert not [c["name"] for c in single if c["secret"] in text], "A CANARY REACHED THE API"
    assert MARKER in text, "no redaction marker in what the API returned (a dead pipeline?)"
    say(f"{len(single)} canary secrets are absent from the API's answer and the marker is present")


def second_layer() -> None:
    """Secrets already IN Loki (stored before the pipeline existed, or past a missed
    pattern) must not come back out through the portal."""
    stamp = now_ns()
    entries: list[tuple[int, str]] = []
    for canary in CANARIES:
        for physical in canary.get("lines") or [canary["line"]]:
            entries.append((stamp + len(entries), physical.replace(SECRET_PLACEHOLDER, canary["secret"])))
    push([(labels(RAW), entries)])
    stored = json.loads(
        urllib.request.urlopen(
            f"{LOKI}/loki/api/v1/query_range?"
            + urllib.parse.urlencode({"query": f'{{service="{RAW}"}}', "limit": "500"}),
            timeout=15,
        ).read()
    )["data"]["result"]
    raw = " ".join(v[1] for s in stored for v in s["values"])
    assert all(c["secret"] in raw for c in CANARIES if not c.get("known_gap")), "the raw push did not land"

    got = wait_for("the raw lines through the API", lambda: (r := search(service=RAW, limit=1000))["entries"] and r)
    text = json.dumps(got)
    leaked = [c["name"] for c in CANARIES if not c.get("known_gap") and c["secret"] in text]
    assert not leaked, f"CANARY SECRETS CAME BACK OUT OF THE PORTAL (release blocker): {leaked}"
    assert MARKER in text
    say(f"{len(CANARIES) - 1} single-line canaries pushed RAW into Loki: none comes back, the marker does")
    gap = [c for c in CANARIES if c.get("known_gap")]
    assert all(c["secret"] in text for c in gap), "the multi-line gap no longer leaks: update SECURITY.md §17"
    say("the multi-line PEM block comes back, as SECURITY.md §17 says (a known, pinned limit)")


def filters_and_presets(sources: dict) -> None:
    needle = 'q"uo\\te {x} | ~ `bt` } or {server_id=~".+"} |= "'
    stamp = now_ns()
    push([(labels(HOSTILE), [(stamp, "prefix " + needle + " suffix"), (stamp + 1, "unrelated")])])
    got = wait_for("the hostile line", lambda: (r := search(service=HOSTILE, contains=needle))["entries"] and r)
    assert lines(got) == ["prefix " + needle + " suffix"], lines(got)
    say("a `contains` full of quotes, backslashes, braces and a LogQL fragment matches literally in a real Loki")

    positives = {
        "http_5xx": ['1.2.3.4 - - [x] "GET /o HTTP/1.1" 502 12 "-" "a"', '{"status": 503, "m": 1}'],
        "oom_killed": ["Out of memory: Killed process 12 (php)"],
        "connection_refused": ["connect() failed (111: Connection refused) while connecting"],
        "timeout": ["upstream timed out", "context deadline exceeded"],
        "permission_denied": ["open() failed: Permission denied"],
        "auth_failure": ["Failed password for root from 1.2.3.4"],
        "segfault": ["child 5 exited on signal 11 (SIGSEGV)"],
        "disk_full": ["write failed: No space left on device"],
    }
    negatives = ['1.2.3.4 - - [x] "GET /o HTTP/1.1" 200 5000 "-" "a"', '{"status": 200}', "all good"]
    assert {p["id"] for p in sources["presets"]} == set(positives)
    stamp = now_ns()
    every = [line for group in positives.values() for line in group] + negatives
    push([(labels(PRESETS), [(stamp + i, line) for i, line in enumerate(every)])])
    wait_for("the preset lines", lambda: len(lines(search(service=PRESETS, limit=100))) == len(every))
    for preset_id, want in positives.items():
        got = lines(search(service=PRESETS, pattern_preset=preset_id, limit=100))
        assert sorted(got) == sorted(want), (preset_id, got)
    say("all 8 presets are accepted by a real Loki and each returns exactly its own lines")

    both = lines(search(service=PRESETS, pattern_preset="timeout", contains="deadline"))
    assert both == ["context deadline exceeded"], both
    say("`contains` and a preset together are ANDed")


def paging() -> None:
    """Real Loki's ties, across two streams, paged both ways at several sizes."""
    base = now_ns() - 5 * 60 * 10**9
    a = [(base, f"{TAG}a-{i}") for i in range(4)] + [(base - 10**9, f"{TAG}a-early"), (base + 10**9, f"{TAG}a-late")]
    b = [(base, f"{TAG}b-{i}") for i in range(3)] + [(base + 2 * 10**9, f"{TAG}b-late")]
    push([(labels(f"smoke-page-a-{RUN}"), a), (labels(f"smoke-page-b-{RUN}"), b)])
    expected = {line for _, line in a + b}
    wait_for("the paging lines", lambda: len(lines(search(contains=TAG, limit=100))) == len(expected))

    for direction in ("backward", "forward"):
        whole = lines(search(contains=TAG, direction=direction, limit=100))
        assert sorted(whole) == sorted(expected)
        for size in (1, 2, 3, 4, 5, 9):
            seen: list[str] = []
            cursor = None
            for _ in range(30):
                body = {"contains": TAG, "direction": direction, "limit": size}
                if cursor:
                    body["cursor"] = cursor
                page = search(**body)
                seen += lines(page)
                cursor = page["page"]["next_cursor"]
                if cursor is None:
                    break
            assert len(seen) == len(set(seen)), f"{direction}/{size}: a line was repeated: {seen}"
            assert seen == whole, f"{direction}/{size}: paging disagrees with one shot:\n{seen}\n{whole}"
    say(f"{len(expected)} lines, 4+3 sharing one nanosecond across two streams: paging at "
        "sizes 1-9, both directions, repeats nothing and skips nothing in a real Loki")


def scope_and_states() -> None:
    stranger = str(uuid.uuid4())
    status, body = call("POST", "/logs/query", {**window(), "server_ids": [SERVER, stranger]})
    assert status == 200 and isinstance(body, dict)
    assert body["excluded_by_scope_count"] == 1 and body["servers_searched"] == 1
    say("an unknown server id is dropped and counted (excluded_by_scope_count=1), never queried")

    status, listing = call("GET", "/servers?limit=50")
    assert status == 200 and isinstance(listing, dict)
    quiet = next(s["id"] for s in listing["items"] if s["id"] != SERVER)
    status, empty = call("POST", "/logs/query", {**window(), "server_ids": [quiet]})
    assert status == 200 and isinstance(empty, dict)
    assert empty["entries"] == [] and [i["state"] for i in empty["ingestion"]] == ["silent"], empty
    say("a registered server that has never shipped is `silent`: the empty page is NOT 'no errors'")

    for smuggled in ("query", "logql", "selector"):
        status, _ = call("POST", "/logs/query", {**window(), "server_ids": [SERVER], smuggled: '{job=~".+"}'})
        assert status == 422, (smuggled, status)
    status, _ = call("POST", "/logs/query", window())
    assert status == 422
    say("a LogQL-carrying field and an unbounded query are both 422")


def outage() -> None:
    status, body = call("POST", "/logs/query", {**window(), "server_ids": [SERVER]})
    assert status == 503, f"Loki is down but the API answered {status}: {str(body)[:200]}"
    assert isinstance(body, dict) and body["error"]["code"] == "provider_unavailable", body
    assert "entries" not in json.dumps(body)
    say("Loki stopped: 503 provider_unavailable, and nothing that could read as an empty result")
    status, _ = call("GET", "/logs/sources")
    assert status == 200, "the static catalogue should not depend on Loki"


def recovered() -> None:
    got = wait_for(
        "the API to recover after Loki came back",
        lambda: (r := (call("POST", "/logs/query", {**window(), "server_ids": [SERVER]})))[0] == 200 and r[1],
        seconds=120,
    )
    assert isinstance(got, dict) and got["entries"], "recovered but no lines"
    say("Loki back: the same query answers 200 with lines (the circuit stayed closed)")


def run_search() -> None:
    sources = catalogue()
    real_pipeline()
    second_layer()
    filters_and_presets(sources)
    paging()
    scope_and_states()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    modes = {"search": run_search, "outage": outage, "recovered": recovered}
    if mode not in modes:
        sys.exit("usage: smoke_logs_api.py search | outage | recovered")
    modes[mode]()
