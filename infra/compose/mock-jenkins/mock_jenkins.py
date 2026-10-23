"""A stand-in for the parts of Jenkins' REST API that DCP uses (M5.1, ``OPEN-128``).

**A DEVELOPMENT AID, not Jenkins** (CLAUDE.md section 7). It answers the calls DCP's client makes --
crumb, ``buildWithParameters``, queue item, build status, progressive console -- and a request
recorder and a mode switch so tests can assert and break things. It checks Basic auth. Passing
against it proves DCP's behaviour, not that a real Jenkins works: crumb rules, queue-item expiry and
plugin-specific build parameters differ by Jenkins version and plugin set.

Standard library only, so it adds no dependency and the same WSGI callable serves two ways: a
``wsgiref`` server inside the Compose stack, and ``httpx.WSGITransport`` in tests (the checked
outbound path refuses loopback, so the in-process tests never open a socket).

The control endpoints (``/__mock/*``) exist only here; no Jenkins has them.

Modes (``mode``): ``ok``; ``outage`` (every call ``503``); ``slow`` (each call waits ``delay``
seconds); ``queue_cancelled``; ``build_failure``; ``lose_queue_item`` (the queue item ``404``s, as
Jenkins' does once an item leaves its queue); ``redirect`` (``buildWithParameters`` answers ``302``
to the cloud metadata address, which a client must not follow).
Crumb (``crumb``): ``off`` (no issuer, ``404``); ``issue`` (issued, not enforced); ``enforce``
(issued, and a POST without it is ``403``).
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs

JOB_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
CRUMB_FIELD = "Jenkins-Crumb"
CRUMB_VALUE = "0123456789abcdef0123456789abcdef"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
#: Planted so M5.3's redaction test has something to fail on. Obviously fake.
CONSOLE_CANARY = "DEPLOY_PASSWORD=mock-canary-jenkins-0001"
MODES = {
    "ok",
    "outage",
    "slow",
    "queue_cancelled",
    "build_failure",
    "lose_queue_item",
    "redirect",
}
CRUMBS = {"off", "issue", "enforce"}


class MockJenkins:
    def __init__(
        self,
        *,
        user: str = "dcp",
        token: str = "mock-jenkins-token",  # noqa: S107 -- a development aid's fixed credential
        jobs: tuple[str, ...] = ("demo/hello-pipeline", "demo/hello-rollback"),
        queue_polls: int = 2,
        build_polls: int = 2,
        sleep: Any = time.sleep,
    ) -> None:
        self.user, self.token, self.jobs = user, token, set(jobs)
        self.queue_polls, self.build_polls = queue_polls, build_polls
        self._sleep = sleep
        self._lock = threading.Lock()
        self.reset()

    # -- state ---------------------------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self.mode, self.crumb, self.delay = "ok", "issue", 0.2
            self.requests: list[dict[str, Any]] = []
            self.queue: dict[int, dict[str, Any]] = {}
            self.builds: dict[tuple[str, int], dict[str, Any]] = {}
            self.next_queue_id, self.next_build = 100, {}

    # -- WSGI ----------------------------------------------------------------------------

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ["REQUEST_METHOD"]
        path = environ.get("PATH_INFO", "")
        query = parse_qs(environ.get("QUERY_STRING", ""))
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        status, headers, payload = self._route(environ, method, path, query, body)
        start_response(status, [(k, v) for k, v in headers.items()])
        return [payload]

    def _reply(
        self, status: str, data: Any = None, *, text: str | None = None, **headers: str
    ) -> tuple[str, dict[str, str], bytes]:
        if text is not None:
            return status, {"Content-Type": "text/plain; charset=utf-8", **headers}, text.encode()
        return (
            status,
            {"Content-Type": "application/json", **headers},
            json.dumps(data if data is not None else {}).encode(),
        )

    def _route(
        self,
        environ: dict[str, Any],
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: bytes,
    ) -> tuple[str, dict[str, str], bytes]:
        if path == "/healthz":
            return self._reply("200 OK", text="ok")
        if path.startswith("/__mock/"):
            return self._control(method, path, body)

        params = {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        authorised = self._authorised(environ)
        with self._lock:
            self.requests.append(
                {
                    "method": method,
                    "path": path,
                    "query": {k: v[-1] for k, v in query.items()},
                    "params": params,
                    "authorised": authorised,
                    "crumb": environ.get("HTTP_JENKINS_CRUMB") is not None,
                }
            )
        if self.mode == "outage":
            return self._reply("503 Service Unavailable", text="down")
        if self.mode == "slow":
            self._sleep(self.delay)
        if not authorised:
            return self._reply(
                "401 Unauthorized", text="no", **{"WWW-Authenticate": 'Basic realm="mock"'}
            )

        if path == "/api/json":
            return self._reply("200 OK", {"mode": "NORMAL", "nodeName": ""})
        if path == "/crumbIssuer/api/json":
            if self.crumb == "off":
                return self._reply("404 Not Found", text="no crumb issuer")
            return self._reply("200 OK", {"crumb": CRUMB_VALUE, "crumbRequestField": CRUMB_FIELD})
        if match := re.fullmatch(r"/queue/item/(\d+)/api/json", path):
            return self._queue_item(int(match.group(1)))
        job = re.fullmatch(rf"((?:/job/{JOB_SEGMENT})+)(/.*)", path)
        if job is None:
            return self._reply("404 Not Found", text="not found")
        job_path = "/".join(job.group(1).split("/job/")[1:])
        if job_path not in self.jobs:
            return self._reply("404 Not Found", text="no such job")
        rest = job.group(2)
        if rest == "/buildWithParameters" and method == "POST":
            return self._trigger(environ, job_path, params)
        if match := re.fullmatch(r"/(\d+)/api/json", rest):
            return self._build(job_path, int(match.group(1)))
        if match := re.fullmatch(r"/(\d+)/logText/progressiveText", rest):
            return self._console(job_path, int(match.group(1)), query)
        return self._reply("404 Not Found", text="not found")

    # -- handlers ------------------------------------------------------------------------

    def _authorised(self, environ: dict[str, Any]) -> bool:
        header = environ.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, token = base64.b64decode(header[6:]).decode().partition(":")
        except ValueError:
            return False
        return user == self.user and token == self.token

    def _trigger(
        self, environ: dict[str, Any], job_path: str, params: dict[str, str]
    ) -> tuple[str, dict[str, str], bytes]:
        if self.crumb == "enforce" and environ.get("HTTP_JENKINS_CRUMB") != CRUMB_VALUE:
            return self._reply("403 Forbidden", text="No valid crumb was included in the request")
        if self.mode == "redirect":
            return self._reply(
                "302 Found", text="", Location="http://169.254.169.254/latest/meta-data/"
            )
        with self._lock:
            queue_id = self.next_queue_id
            self.next_queue_id += 1
            self.queue[queue_id] = {"job": job_path, "reads": 0, "params": params}
        host = environ.get("HTTP_HOST", "mock-jenkins:8080")
        return self._reply("201 Created", text="", Location=f"http://{host}/queue/item/{queue_id}/")

    def _queue_item(self, queue_id: int) -> tuple[str, dict[str, str], bytes]:
        with self._lock:
            item = self.queue.get(queue_id)
            if item is None or self.mode == "lose_queue_item":
                return self._reply("404 Not Found", text="gone")
            if self.mode == "queue_cancelled":
                return self._reply("200 OK", {"cancelled": True, "why": None})
            item["reads"] += 1
            if item["reads"] <= self.queue_polls:
                return self._reply("200 OK", {"cancelled": False, "why": "waiting", "id": queue_id})
            if "build" not in item:
                number = self.next_build.get(item["job"], 0) + 1
                self.next_build[item["job"]] = number
                item["build"] = number
                self.builds[(item["job"], number)] = {"reads": 0, "params": item["params"]}
            number = item["build"]
        return self._reply(
            "200 OK",
            {
                "cancelled": False,
                "id": queue_id,
                "executable": {"number": number, "url": f"/job/{item['job']}/{number}/"},
            },
        )

    def _build(self, job_path: str, number: int) -> tuple[str, dict[str, str], bytes]:
        with self._lock:
            build = self.builds.get((job_path, number))
            if build is None:
                return self._reply("404 Not Found", text="no such build")
            build["reads"] += 1
            building = build["reads"] <= self.build_polls
        result = None if building else ("FAILURE" if self.mode == "build_failure" else "SUCCESS")
        return self._reply(
            "200 OK",
            {
                "building": building,
                "result": result,
                "timestamp": 1_700_000_000_000,
                "duration": 0 if building else 4_200,
                "actions": [
                    {"_class": "hudson.model.ParametersAction"},
                    {"lastBuiltRevision": {"SHA1": COMMIT_SHA}},
                ],
            },
        )

    def _console(
        self, job_path: str, number: int, query: dict[str, list[str]]
    ) -> tuple[str, dict[str, str], bytes]:
        with self._lock:
            build = self.builds.get((job_path, number))
            if build is None:
                return self._reply("404 Not Found", text="no such build")
            building = build["reads"] <= self.build_polls
        lines = [
            "Started by user dcp",
            f"Building {job_path} #{number}",
            CONSOLE_CANARY,
            "Finished: FAILURE" if self.mode == "build_failure" else "Finished: SUCCESS",
        ]
        full = ("\n".join(lines) + "\n").encode()
        try:
            start = max(0, int(query.get("start", ["0"])[0]))
        except ValueError:
            start = 0
        return self._reply(
            "200 OK",
            text=full[start:].decode(),
            **{"X-Text-Size": str(len(full)), "X-More-Data": "true" if building else "false"},
        )

    def _control(self, method: str, path: str, body: bytes) -> tuple[str, dict[str, str], bytes]:
        if path == "/__mock/requests" and method == "GET":
            with self._lock:
                return self._reply("200 OK", {"requests": list(self.requests)})
        if path == "/__mock/reset" and method == "POST":
            self.reset()
            return self._reply("200 OK", {"ok": True})
        if path == "/__mock/mode" and method == "POST":
            try:
                spec = json.loads(body or b"{}")
            except ValueError:
                return self._reply("400 Bad Request", {"error": "not json"})
            mode, crumb = spec.get("mode", self.mode), spec.get("crumb", self.crumb)
            if mode not in MODES or crumb not in CRUMBS:
                return self._reply("400 Bad Request", {"error": "unknown mode"})
            self.mode, self.crumb = mode, crumb
            self.delay = float(spec.get("delay", self.delay))
            return self._reply("200 OK", {"mode": self.mode, "crumb": self.crumb})
        return self._reply("404 Not Found", text="not found")


def app_from_environment() -> MockJenkins:
    return MockJenkins(
        user=os.environ.get("MOCK_JENKINS_USER") or "dcp",
        token=os.environ.get("MOCK_JENKINS_TOKEN") or "mock-jenkins-token",  # noqa: S105
    )


if __name__ == "__main__":  # pragma: no cover -- the container entry point
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

    class _Threaded(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    class _Quiet(WSGIRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    make_server("0.0.0.0", 8080, app_from_environment(), _Threaded, _Quiet).serve_forever()  # noqa: S104
