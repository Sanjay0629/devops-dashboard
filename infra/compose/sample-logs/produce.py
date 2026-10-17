"""Sample log producer: a DEVELOPMENT AID, not a deployment artefact (M3.1).

Writes the formats Alloy has to cope with into a directory laid out like a
managed server's, so the same file globs work here and there:

    nginx/access.log  nginx/error.log  php-fpm/php-fpm.log  syslog
    app/sample-api.log                       (JSON lines)
    docker/<id>/<id>-json.log                (the json-file driver's format)

Every round carries the canary secrets in ``canaries.json`` in every source, plus
the awkward cases the pipeline must survive: a request id, user id and path in
the line (high cardinality, which must never become a label), severities in
every spelling, an unmapped severity and a hostile one, and a hostile container
service name. A canary that never reached the file would make "absent from Loki"
mean nothing, so backend/tests/test_sample_logs.py asserts each one is emitted.

Standard library only; no secret here is real.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

CANARIES = json.loads((Path(__file__).parent / "canaries.json").read_text(encoding="utf-8"))
SECRET = "<<SECRET>>"  # noqa: S105 - the template token, not a secret
ROTATE_AT_BYTES = 2_000_000
CONTAINER_ID = "0123456789abcdef" * 4  # 64 hex, like a real container id

# (spelling in the source, what the pipeline should make of it). "unmapped" and
# "hostile" must come out as level=unknown, never as themselves.
APP_LEVELS = ["DEBUG", "info", "WARNING", "err", "fatal", "TRACE", "x<script>alert(1)</script>"]
PHP_LEVELS = ["NOTICE", "WARNING", "ERROR"]
NGINX_ERROR_LEVELS = ["notice", "warn", "error", "crit", "emerg"]
# Service names a container might carry. The last is hostile: it must collapse
# to service=other rather than become a label value.
DOCKER_SERVICES = ["sample-api", "sample-worker", "Weird Name; DROP TABLE"]


def canary_lines() -> list[str]:
    """Every canary as the physical lines it occupies in a log."""
    out: list[str] = []
    for canary in CANARIES:
        template = canary.get("lines") or [canary["line"]]
        out.extend(line.replace(SECRET, canary["secret"]) for line in template)
    return out


def render_round(n: int, now: datetime) -> dict[str, list[str]]:
    """The lines of round ``n``, by path relative to the log root."""
    request_id = uuid.uuid4().hex
    user_id = f"user-{n % 97}-{uuid.uuid4().hex[:6]}"
    canaries = canary_lines()
    iso = now.isoformat(timespec="microseconds").replace("+00:00", "Z")

    nginx_access = [
        f"203.0.113.{n % 250 + 1} - - [{now:%d/%b/%Y:%H:%M:%S} +0000] "
        f'"GET /orders/{request_id} HTTP/1.1" {status} {512 + n} "-" "sample-agent/1.0"'
        for status in (200, 404, 502)
    ] + [
        # An access log has no free-text field except the ones a client controls,
        # which is also where a secret really does turn up (a header echoed back).
        f'203.0.113.9 - - [{now:%d/%b/%Y:%H:%M:%S} +0000] "GET /health HTTP/1.1" 200 2 "-" "{line}"'
        for line in canaries
    ]
    nginx_error = [
        f"{now:%Y/%m/%d %H:%M:%S} [{level}] 12#12: *{n} sample-logs round {n} request {request_id}"
        for level in NGINX_ERROR_LEVELS
    ] + [f"{now:%Y/%m/%d %H:%M:%S} [error] 12#12: *{n} {line}" for line in canaries]
    php = [
        f"[{now:%d-%b-%Y %H:%M:%S}] {level}: [pool www] child {n} sample-logs round {n}"
        for level in PHP_LEVELS
    ] + [f"[{now:%d-%b-%Y %H:%M:%S}] ERROR: [pool www] {line}" for line in canaries]
    syslog = [
        f"{now:%b %e %H:%M:%S} sample-host sample-daemon[{n}]: sample-logs round {n} {line}"
        for line in ["heartbeat", *canaries]
    ]

    app = [
        json.dumps(
            {
                "ts": iso,
                "level": level,
                "message": f"sample-logs round {n}",
                "request_id": request_id,
                "user_id": user_id,
                "trace_id": uuid.uuid4().hex,
            }
        )
        for level in APP_LEVELS
    ] + [
        json.dumps({"ts": iso, "level": "error", "message": line, "request_id": request_id})
        for line in canaries
    ]

    docker: list[str] = []
    for service in DOCKER_SERVICES:
        for line in [f"sample-logs round {n} from {service}", *canaries]:
            docker.append(
                json.dumps(
                    {
                        "log": line + "\n",
                        "stream": "stdout",
                        "time": iso,
                        "attrs": {"service": service},
                    }
                )
            )

    return {
        "nginx/access.log": nginx_access,
        "nginx/error.log": nginx_error,
        "php-fpm/php-fpm.log": php,
        "syslog": syslog,
        "app/sample-api.log": app,
        f"docker/{CONTAINER_ID}/{CONTAINER_ID}-json.log": docker,
    }


def write_round(root: Path, n: int, now: datetime | None = None) -> None:
    for relative, lines in render_round(n, now or datetime.now(UTC)).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate the way logrotate does (rename, start afresh), so a tailer is
        # exercised on rotation and a dev volume cannot grow without bound.
        if path.exists() and path.stat().st_size > ROTATE_AT_BYTES:
            path.replace(path.with_name(path.name + ".1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main() -> None:
    root = Path(os.environ.get("SAMPLE_LOG_ROOT", "/var/log/sample"))
    interval = float(os.environ.get("SAMPLE_INTERVAL_SECONDS", "10"))
    rounds = int(os.environ.get("SAMPLE_ROUNDS", "0"))  # 0 = until stopped
    n = 0
    while rounds == 0 or n < rounds:
        n += 1
        write_round(root, n)
        # The healthcheck reads this: a producer that stopped writing is unhealthy.
        (root / ".heartbeat").write_text(str(n), encoding="utf-8")
        print(f"sample-logs round {n} written", flush=True)  # noqa: T201
        if rounds == 0 or n < rounds:
            time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
