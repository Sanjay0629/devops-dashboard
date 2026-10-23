"""Assertions for scripts/smoke-test.sh step 18i (M5.1): the Jenkins client over the REAL path.

Run INSIDE the ``backend`` container (``exec -T backend python - < scripts/smoke_jenkins.py``), so
it uses the backend's own settings and reaches the ``mock-jenkins`` service over the project
network. What the unit suite cannot show, because the checked outbound path refuses loopback and so
the unit tests mount the mock's WSGI app on an injected transport:

* a name is resolved, **every address is checked**, and the connection goes to the checked address
  (the mock lives on a private Docker address that ``DCP_SSRF_ALLOWED_NETWORKS`` admits);
* the allowlist refuses a host that is not listed, and the address check refuses loopback and the
  metadata address, on this call site;
* a trigger arrives **exactly once**, with exactly the parameters it was given;
* queue -> build -> console works end to end, an outage is a closed reason and opens the circuit,
  and §26.1's row follows.

``mock-jenkins`` is a development aid, not Jenkins. Passing here proves DCP's behaviour against
that mock; it is not evidence that a real Jenkins works.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
import uuid

MOCK = "http://mock-jenkins:8080"
JOB = "demo/hello-pipeline"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label + (f" ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def mock(path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(  # noqa: S310 -- the mock's own control endpoint
        f"{MOCK}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())


def main() -> int:
    from app.core.config import get_settings
    from app.core.ssrf import DestinationRefusedError, UrlNotAllowedError
    from app.core.ssrf import pin_to_checked_address, resolve_checked
    from app.deployments import jenkins as module
    from app.deployments.jenkins import JenkinsClient, JenkinsError
    from app.deployments.status import jenkins_row

    settings = get_settings()
    check("DCP_JENKINS_URL is configured for this run", settings.jenkins_url == MOCK)
    mock("/__mock/reset", {})
    module.reset_breaker()
    client = JenkinsClient(settings)

    print("the real network path")
    started = client.probe()
    check("an authenticated probe reaches the mock", started is not None)
    check(
        "the probe was authorised and read only",
        [(r["method"], r["authorised"]) for r in mock("/__mock/requests")["requests"]]
        == [("GET", True)],
    )

    print("the allowlist and the address check, on this call site")
    try:
        pin_to_checked_address("http://not-listed.example.invalid:8080/api/json")
        check("an unlisted host is refused by the allowlist", False)
    except UrlNotAllowedError:
        check("an unlisted host is refused by the allowlist", True)
    for host in ("localhost", "metadata.invalid"):
        try:
            resolve_checked(host, 8080, resolver=lambda h, p: ["169.254.169.254"])
            check(f"the metadata address is refused for {host}", False)
        except DestinationRefusedError:
            check(f"the metadata address is refused for {host}", True)
    try:
        resolve_checked("localhost", 8080)
        check("loopback is refused", False)
    except DestinationRefusedError:
        check("loopback is refused", True)

    print("trigger, queue, build, console")
    mock("/__mock/reset", {})
    parameters = {"GIT_REF": "main", "DCP_DEPLOYMENT_ID": str(uuid.uuid4())}
    ref = client.trigger(JOB, parameters)
    posts = [r for r in mock("/__mock/requests")["requests"] if r["method"] == "POST"]
    check("the trigger arrived exactly once", len(posts) == 1, str(len(posts)))
    check("with exactly the given parameters", posts and posts[0]["params"] == parameters)
    check("and the crumb the Jenkins issued", posts and posts[0]["crumb"] is True)

    deadline = time.monotonic() + 30
    state = client.queue_item(ref.queue_id)
    while state.kind == "waiting" and time.monotonic() < deadline:
        state = client.queue_item(ref.queue_id)
    check("the queue item resolves to a build number", state.kind == "started" and state.build_number == 1)

    build = client.build(JOB, 1)
    while build.building and time.monotonic() < deadline:
        build = client.build(JOB, 1)
    check("the build finishes with a result and a commit", build.result == "SUCCESS" and build.commit_sha is not None)
    chunk = client.console(JOB, 1)
    check("the console is returned, raw and unredacted by the client", "mock-canary-jenkins-0001" in chunk.text)

    print("credentials")
    wrong = JenkinsClient(settings, secret_backend=type("B", (), {"get": lambda self, name: "wrong"})())
    try:
        wrong.probe()
        check("a wrong token is refused", False)
    except JenkinsError as error:
        check("a wrong token is auth_failed", error.reason == "auth_failed", error.reason)

    print("an outage, the circuit and the integration row")
    mock("/__mock/mode", {"mode": "outage"})
    try:
        client.probe()
        check("an outage is an error", False)
    except JenkinsError as error:
        check("an outage is a closed reason", error.reason == "provider_error", error.reason)
    row = asyncio.run(jenkins_row())
    check("the integration row says unavailable, with a reason and no URL",
          row.status == "unavailable" and "://" not in (row.last_error_message or ""), str(row.last_error_message))
    mock("/__mock/mode", {"mode": "ok"})
    module.reset_breaker()
    row = asyncio.run(jenkins_row())
    check("and recovers", row.status == "ok" and row.circuit_state == "closed", row.status)

    mock("/__mock/reset", {})
    print()
    print("FAILED: " + ", ".join(failures) if failures else "all Jenkins smoke assertions passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
