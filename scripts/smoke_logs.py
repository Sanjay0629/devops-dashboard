"""Assertions for scripts/smoke-test.sh steps 18d and 18e (M3.1).

Run INSIDE the ``sample-logs`` container (stdin: ``exec -T sample-logs python - MODE``),
so it reaches Loki over the project network and reads the very canary corpus the
producer used. Modes:

  ingestion  the file pipeline: labels, closed values, mappings, canaries
  journal    the journald pipeline against a real systemd journal
  absent H   no stream with hostname H has reached Loki
  appid-dropped H   other sources under H arrived, `application` did not

Every "absent" assertion is paired with a "present" one for the same pipeline,
because absence on its own is what a dead pipeline looks like.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request

LOKI = "http://loki:3100"
ALLOWED = {
    "environment", "server_id", "hostname", "application_id",
    "service", "source_type", "level",
}  # fmt: skip
LEVELS = {"debug", "info", "warn", "error", "critical", "unknown"}
FILE_SOURCES = {"application", "docker", "nginx", "php_fpm", "syslog"}
SOURCE_TYPES = FILE_SOURCES | {"journald"}
SERVICE_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MARKER = "[REDACTED]"
CANARIES = json.load(open("/sample-logs/canaries.json", encoding="utf-8"))  # noqa: SIM115


def get(path: str, **params: str) -> dict:
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    with urllib.request.urlopen(f"{LOKI}{path}{query}", timeout=15) as response:
        return json.loads(response.read())


def query(logql: str, limit: int = 1000) -> list[tuple[dict, str]]:
    result = get("/loki/api/v1/query_range", query=logql, limit=str(limit))["data"]["result"]
    return [(s["stream"], v[1]) for s in result for v in s["values"]]


def wait_for(description: str, probe, seconds: int = 120):
    deadline = time.time() + seconds
    while True:
        value = probe()
        if value:
            return value
        if time.time() > deadline:
            raise AssertionError(f"timed out after {seconds}s waiting for {description}")
        time.sleep(3)


def say(text: str) -> None:
    sys.stdout.write(f"  {text}\n")


def check_stream_labels(rows: list[tuple[dict, str]], sources: set[str]) -> None:
    seen: set[str] = set()
    for stream, _ in rows:
        names = set(stream)
        assert names <= ALLOWED, f"labels outside the allowlist: {sorted(names - ALLOWED)}"
        required = ALLOWED - {"application_id"}
        assert required <= names, f"a stream is missing {sorted(required - names)}: {stream}"
        assert stream["level"] in LEVELS, stream
        assert stream["source_type"] in sources, stream
        assert stream["service"] == "other" or SERVICE_SHAPE.fullmatch(stream["service"]), stream
        assert UUID.fullmatch(stream["server_id"]), stream
        # An absent label is not an empty one: only the application source has it.
        assert ("application_id" in names) == (stream["source_type"] == "application"), stream
        seen.add(stream["source_type"])
    assert seen == sources or sources == {"journald"}, f"sources seen {sorted(seen)}"


def ingestion() -> None:
    sample = '{hostname="sample-host.internal.example"}'

    def all_sources_flowing():
        rows = query(sample + ' |= "sample-logs round"')
        got = {s["source_type"] for s, _ in rows}
        return rows if FILE_SOURCES <= got else None

    wait_for("all five file sources to reach Loki", all_sources_flowing)
    # Wait for the application source to carry a redacted line, so the canary
    # checks below look at lines that have certainly been through the pipeline.
    wait_for("a redacted line from every source", lambda: len(
        {s["source_type"] for s, _ in query(sample + f' |= "{MARKER}"')} & FILE_SOURCES
    ) == len(FILE_SOURCES))

    labels = set(get("/loki/api/v1/labels")["data"])
    assert labels <= ALLOWED, f"Loki holds labels outside the allowlist: {sorted(labels - ALLOWED)}"
    for unbounded in ("filename", "job", "service_name", "request_id", "container_id"):
        assert unbounded not in labels, unbounded
    say(f"every label Loki holds is on the allowlist: {sorted(labels)}")

    rows = query(sample)
    check_stream_labels(rows, FILE_SOURCES)
    say(f"{len(rows)} entries: every stream has exactly the allowed labels and closed values")
    assert {"environment": "development"} == {"environment": rows[0][0]["environment"]}

    def levels(logql: str) -> set[str]:
        found = {s["level"] for s, _ in query(logql)}
        assert found, f"no entries for {logql}"
        return found

    # A severity is mapped, or it is `unknown`. Never guessed, never passed through.
    expect = {
        'app "DEBUG"': ("{source_type=\"application\"} |= `\"level\": \"DEBUG\"`", {"debug"}),
        'app "info"': ("{source_type=\"application\"} |= `\"level\": \"info\"`", {"info"}),
        'app "WARNING"': ("{source_type=\"application\"} |= `\"level\": \"WARNING\"`", {"warn"}),
        'app "err"': ("{source_type=\"application\"} |= `\"level\": \"err\"`", {"error"}),
        'app "fatal"': ("{source_type=\"application\"} |= `\"level\": \"fatal\"`", {"critical"}),
        'app "TRACE"': ("{source_type=\"application\"} |= `\"level\": \"TRACE\"`", {"unknown"}),
        "app hostile": ('{source_type="application"} |= "<script>"', {"unknown"}),
        "php WARNING": ('{source_type="php_fpm"} |= "WARNING: [pool www] child"', {"warn"}),
        "php ERROR": ('{source_type="php_fpm"} |= "ERROR: [pool www] child"', {"error"}),
        "nginx [crit]": ('{source_type="nginx"} |= "[crit] 12#12"', {"critical"}),
        "nginx [notice]": ('{source_type="nginx"} |= "[notice] 12#12"', {"info"}),
        "nginx access (no level)": ('{source_type="nginx"} |= "GET /orders"', {"unknown"}),
        "syslog (no level)": ('{source_type="syslog"} |= "heartbeat"', {"unknown"}),
    }
    for name, (logql, want) in expect.items():
        got = levels(logql)
        assert got == want, f"{name}: level {sorted(got)}, expected {sorted(want)}"
    say(f"{len(expect)} severity mappings hold; an access log, TRACE and a hostile level are all `unknown`")

    services = {s["service"] for s, _ in query(sample)}
    assert "other" in services and "sample-api" in services, services
    hostile = {s["service"] for s, _ in query(sample + ' |= "Weird Name"')}
    assert hostile == {"other"}, f"a hostile container name became a label value: {hostile}"
    say("a container named `Weird Name; DROP TABLE` is service=`other`; the label values are bounded")

    for source in sorted(FILE_SOURCES):
        marked = query('{source_type="%s"} |= "%s"' % (source, MARKER), 5)
        assert marked, f"no redaction marker in {source}: the pipeline may be dead"
    leaked = []
    for canary in CANARIES:
        holding = query(sample + ' |= "%s"' % canary["secret"], 5)
        if canary.get("known_gap"):
            assert holding, f"{canary['name']} no longer leaks: update SECURITY.md §17 and the corpus"
        elif holding:
            leaked.append(canary["name"])
    assert not leaked, f"CANARY SECRETS REACHED LOKI (release blocker): {leaked}"
    single = [c["name"] for c in CANARIES if not c.get("known_gap")]
    say(f"{len(single)} canary secrets are absent from Loki in every source, and the marker is "
        f"present in all {len(FILE_SOURCES)} (a dead pipeline would also show absence)")
    say("the multi-line PEM canary leaks, as SECURITY.md §17 says it does (a known, pinned limit)")

    for stream, line in query('{source_type="application"} |= "%s"' % MARKER, 200):
        json.loads(line)
    say("redacted JSON application lines still parse")


def journal() -> None:
    host = '{hostname="journal-host.internal.example"}'
    marker = "m31 journal test line"

    rows = wait_for("journald entries", lambda: query(host, 200))
    wait_for("the test line", lambda: query(host + f' |= "{marker}"', 5))
    check_stream_labels(rows, {"journald"})
    assert {s["source_type"] for s, _ in rows} == {"journald"}
    say(f"{len(rows)} journald entries: only the allowed labels, closed values, no application_id")

    services = {s["service"] for s, _ in rows}
    assert "node-exporter.service" in services, services
    assert "other" in services, f"a non-unit entry should be service=other: {services}"
    say(f"a real unit keeps its name ({sorted(services - {'other'})[:3]}…); a non-unit entry is `other`")

    err = wait_for("the err-priority line", lambda: query(host + f' |= "{marker} err"', 5))
    assert {s["level"] for s, _ in err} == {"error"}, err
    info = query(host + f' |= "{marker} info"', 5)
    assert {s["level"] for s, _ in info} == {"info"}, info
    say("journal PRIORITY maps: err -> error, info -> info")

    assert not query(host + ' |= "canary-journal-0009"', 5), "the journal canary reached Loki"
    redacted = query(host + f' |= "{marker}" |= "{MARKER}"', 5)
    assert redacted, "the journal canary was not redacted (or the pipeline is dead)"
    say("the journal canary is absent and its line carries the redaction marker")


def appid_dropped(hostname: str) -> None:
    """A bad application_id drops the application source only; the rest still flows."""
    scope = '{hostname="%s"}' % hostname
    others = wait_for("the other sources under that hostname", lambda: query(scope, 50))
    assert "application" not in {s["source_type"] for s, _ in others}, "application lines got through"
    assert not query(scope[:-1] + ',source_type="application"}', 5)
    say(f"{hostname}: {len(others)} entries from other sources arrived, none from `application`")


def absent(hostname: str) -> None:
    hosts = set(get("/loki/api/v1/label/hostname/values")["data"])
    assert hostname not in hosts, f"{hostname} reached Loki: {sorted(hosts)}"
    say(f"nothing from {hostname} is in Loki")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "ingestion":
        ingestion()
    elif mode == "journal":
        journal()
    elif mode == "absent" and len(sys.argv) == 3:
        absent(sys.argv[2])
    elif mode == "appid-dropped" and len(sys.argv) == 3:
        appid_dropped(sys.argv[2])
    else:
        sys.exit("usage: smoke_logs.py ingestion | journal | absent H | appid-dropped H")
