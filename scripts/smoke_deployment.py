"""Assertions for scripts/smoke-test.sh step 18j (M5.2): the deployment worker over the REAL path.

Run INSIDE the ``backend`` container (``exec -T backend python - < scripts/smoke_deployment.py``),
so it uses the backend's own settings and database and reaches the ``mock-jenkins`` service over
the project network. What the integration suite cannot show, because the checked outbound path
refuses loopback and so those tests mount the mock's WSGI app on an injected transport:

* a deployment is claimed, **re-checked against the real settings** (this stack runs in
  demonstration mode, so the worker must refuse and send nothing), then -- with demonstration mode
  switched off *in this process only* -- triggered **once**, over a name that is resolved, address
  checked and pinned, and followed through queue, build and result to ``SUCCEEDED``;
* a second delivery of the same message, a worker abandoned mid-lease, and a Jenkins outage in the
  middle of a poll each leave **one** trigger in the mock's request log;
* the reaper ends a deployment past its deadline without calling Jenkins;
* no token and no URL reaches a row.

The script needs a **working API and worker only for what it reads**: it drives the worker's
functions directly and does not go through Celery, so it proves the code path and the network path,
not the broker wiring (``tests/deployments/test_tasks.py`` covers the wiring's shape).

``mock-jenkins`` is a development aid, not Jenkins. Passing here proves DCP's behaviour against
that mock; it is not evidence that a real Jenkins works. It writes rows to this stack's database
(an application, a target, a user and its deployments) and removes what it can; audit rows are
append-only and stay.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from typing import Any

MOCK = "http://mock-jenkins:8080"
JOB = "demo/hello-pipeline"
TEMPLATE = {
    "fixed": {"TARGET": "staging"},
    "slots": {"GIT_REF": "ref", "DCP_DEPLOYMENT_ID": "deployment_id"},
}
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label + (f" ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def mock(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 -- the mock's own control endpoint
        f"{MOCK}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())  # type: ignore[no-any-return]


def posts() -> list[dict[str, Any]]:
    return [
        r
        for r in mock("/__mock/requests")["requests"]
        if r["method"] == "POST" and r["path"].endswith("/buildWithParameters")
    ]


def main() -> int:
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.session import get_sync_engine
    from app.deployments import execute, policy, poll, reap
    from app.deployments import jenkins as module
    from app.deployments.digest import target_digest
    from app.deployments.jenkins import JenkinsClient

    engine = get_sync_engine()
    tag = uuid.uuid4().hex[:10]
    user, application, target = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    created: list[uuid.UUID] = []

    def row(deployment: uuid.UUID) -> dict[str, Any]:
        with engine.connect() as connection:
            found = connection.execute(
                text("SELECT * FROM deployments WHERE id = :i"), {"i": deployment}
            ).mappings()
            return dict(found.one())

    def new_deployment(status: str = "REQUESTED", deadline: int = 3600) -> uuid.UUID:
        deployment = uuid.uuid4()
        with engine.begin() as connection:
            digest = target_digest(JOB, TEMPLATE)
            connection.execute(
                text(
                    "INSERT INTO deployments (id, application_id, deployment_target_id,"
                    " environment, pipeline_reference, requested_ref, requested_by,"
                    " approval_status, execution_status, idempotency_key, correlation_id,"
                    " target_digest, deadline_at, external_execution_id, queue_item_id)"
                    " VALUES (:id, :a, :t, 'staging', :job, 'main', :u, 'NOT_REQUIRED', :s,"
                    " :k, :c, :d, now() + make_interval(secs => :dl),"
                    " CASE WHEN :running THEN '1' END, NULL)"
                ),
                {
                    "id": deployment, "a": application, "t": target, "job": JOB, "u": user,
                    "s": status, "k": uuid.uuid4().hex, "c": uuid.uuid4(), "d": digest,
                    "dl": deadline, "running": status == "RUNNING",
                },
            )  # fmt: skip
        created.append(deployment)
        return deployment

    def due(deployment: uuid.UUID) -> None:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE deployments SET next_poll_at = now() - interval '1 second'"
                     " WHERE id = :i"),
                {"i": deployment},
            )

    def drive(client: JenkinsClient, deployment: uuid.UUID, limit: int = 40) -> str:
        deadline = time.monotonic() + 90
        for _ in range(limit):
            due(deployment)
            poll.poll_one(engine, client, deployment)
            status = str(row(deployment)["execution_status"])
            if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
                return status
            if time.monotonic() > deadline:
                break
        return str(row(deployment)["execution_status"])

    def audit(deployment: uuid.UUID, action: str) -> list[dict[str, Any]]:
        with engine.connect() as connection:
            found = connection.execute(
                text(
                    "SELECT actor_id, actor_label, outcome, sanitized_metadata FROM audit_events"
                    " WHERE action_type = :a AND target_id = :i"
                ),
                {"a": action, "i": deployment},
            ).mappings()
            return [dict(r) for r in found]

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, identity_provider_issuer,"
                    " identity_provider_subject, status) VALUES (:u, :e, 'Smoke 18j',"
                    " 'https://smoke.invalid', :s, 'ACTIVE')"
                ),
                {"u": user, "e": f"smoke-18j-{tag}@example.invalid", "s": f"smoke-18j-{tag}"},
            )
            connection.execute(
                text(
                    "INSERT INTO user_roles (id, user_id, role_id, scope_definition, granted_by,"
                    " created_at, updated_at) SELECT gen_random_uuid(), :u, id,"
                    " CAST(:scope AS jsonb), :u, now(), now() FROM roles"
                    " WHERE name = 'DEVOPS_ENGINEER'"
                ),
                {
                    "u": user,
                    "scope": json.dumps(
                        {
                            "environment": ["development", "staging", "production", "other"],
                            "cloud_account": ["*"],
                            "server": ["*"],
                            "application": ["*"],
                        }
                    ),
                },
            )
            connection.execute(
                text("INSERT INTO applications (id, name, slug) VALUES (:a, :n, :s)"),
                {"a": application, "n": f"Smoke 18j {tag}", "s": f"smoke-18j-{tag}"},
            )
            connection.execute(
                text(
                    "INSERT INTO deployment_targets (id, application_id, environment,"
                    " pipeline_reference, parameter_template, allowed_ref_patterns, status)"
                    " VALUES (:t, :a, 'staging', :job, CAST(:tpl AS jsonb), ARRAY['^main$'],"
                    " 'ACTIVE')"
                ),
                {"t": target, "a": application, "job": JOB, "tpl": json.dumps(TEMPLATE)},
            )

        settings = get_settings()
        check("DCP_JENKINS_URL is configured for this run", settings.jenkins_url == MOCK)
        client = JenkinsClient(settings)

        print("a worker in demonstration mode refuses, and sends nothing")
        mock("/__mock/reset", {})
        module.reset_breaker()
        if settings.demo_mode:
            refused = new_deployment()
            check(
                "the claim is re-checked against the real settings and fails closed",
                execute.run(engine, refused, client=client) == "FAILED:NOT_CONFIGURED",
            )
            check("and Jenkins saw nothing at all", mock("/__mock/requests")["requests"] == [])
            check(
                "the refusal is audited as a worker denial for the requester",
                any(
                    e["sanitized_metadata"].get("phase") == "worker"
                    for e in audit(refused, "DEPLOYMENT_DENIED")
                ),
            )
        else:
            print("  skip  this stack is not in demonstration mode, so there is nothing to refuse")

        # Demonstration mode off for THIS process only: nothing about the stack changes.
        os.environ["DCP_DEMO_MODE"] = "false"
        get_settings.cache_clear()
        client = JenkinsClient(get_settings())

        print("a deployment, over the real network path")
        mock("/__mock/reset", {})
        deployment = new_deployment()
        check("claimed and triggered once", execute.run(engine, deployment, client=client) == "QUEUED:sent")
        sent = posts()
        check("exactly one trigger arrived", len(sent) == 1, str(len(sent)))
        check(
            "with exactly the rendered parameters and the issued crumb",
            bool(sent)
            and sent[0]["params"]
            == {"TARGET": "staging", "GIT_REF": "main", "DCP_DEPLOYMENT_ID": str(deployment)}
            and sent[0]["crumb"] is True
            and sent[0]["authorised"] is True,
        )
        check("the queue id was recorded before polling", row(deployment)["queue_item_id"] == 100)
        check("it was followed to SUCCEEDED", drive(client, deployment) == "SUCCEEDED")
        done = row(deployment)
        check("with the build number and the commit Jenkins reported",
              done["external_execution_id"] == "1" and done["commit_sha"] is not None)
        check("and the explicit no-checks health result, never an empty one",
              done["health_check_result"] == policy.none_configured())
        check("whose pipeline result is SUCCESS and which is no longer polled",
              done["sanitized_result"].get("pipeline_result") == "SUCCESS"
              and done["next_poll_at"] is None)
        events = [audit(deployment, a) for a in ("DEPLOYMENT_STARTED", "DEPLOYMENT_RUNNING",
                                                   "DEPLOYMENT_COMPLETED")]
        check("each transition is audited once, by the named worker for the requester",
              all(len(e) == 1 and e[0]["actor_id"] == user
                  and e[0]["actor_label"] == "system:deployments-worker" for e in events))
        check("following the build never triggered another", len(posts()) == 1)

        print("a second delivery of the same message")
        mock("/__mock/reset", {})
        again = new_deployment()
        check("the first delivery triggers", execute.run(engine, again, client=client) == "QUEUED:sent")
        check("the second finds it claimed and does nothing",
              execute.run(engine, again, client=client) == "not_claimable")
        check("one trigger in Jenkins' log", len(posts()) == 1, str(len(posts())))
        drive(client, again)

        print("a worker abandoned mid-lease")
        mock("/__mock/reset", {})
        resumed = new_deployment()
        execute.run(engine, resumed, client=client)
        due(resumed)
        check("the due row is leased", resumed in poll.lease_due(engine))
        check("and nobody else may take it while the lease stands", resumed not in poll.lease_due(engine))
        due(resumed)  # the lease expires
        check("a later worker leases it again", resumed in poll.lease_due(engine))
        check("and resumes it to SUCCEEDED", drive(client, resumed) == "SUCCEEDED")
        check("without a second trigger", len(posts()) == 1, str(len(posts())))

        print("a Jenkins outage in the middle of a poll")
        mock("/__mock/reset", {})
        outage = new_deployment()
        execute.run(engine, outage, client=client)
        mock("/__mock/mode", {"mode": "outage"})
        due(outage)
        outcome = poll.poll_one(engine, client, outage)
        stalled = row(outage)
        check("a failed look changes nothing but the schedule", outcome == "QUEUED:poll_failed"
              and stalled["execution_status"] == "QUEUED" and stalled["completed_at"] is None, outcome)
        check("and records the closed reason, never Jenkins' words",
              stalled["last_poll_error"] == "provider_error" and stalled["poll_attempts"] == 1)
        mock("/__mock/mode", {"mode": "ok"})
        module.reset_breaker()
        check("it recovers and finishes", drive(client, outage) == "SUCCEEDED")
        healed = row(outage)
        check("with the failure counters cleared",
              healed["last_poll_error"] is None and healed["poll_attempts"] == 0)
        check("and still one trigger", len(posts()) == 1)

        print("the deadline")
        mock("/__mock/reset", {})
        late = new_deployment(status="RUNNING", deadline=-5)
        outcome_map = reap.reap(engine, enqueue=lambda _id: None)
        ended = row(late)
        check("the reaper ends a deployment past its deadline", outcome_map["timed_out"] >= 1
              and ended["execution_status"] == "FAILED"
              and ended["failure_reason"] == "DEPLOYMENT_TIMED_OUT", str(outcome_map))
        check("and says a build may still be running", ended["sanitized_result"].get(
            "build_may_still_be_running") is True
              and ended["sanitized_result"].get("reconciliation_required") is True)
        check("without calling Jenkins", mock("/__mock/requests")["requests"] == [])

        print("nothing sensitive reaches a row")
        rendered = json.dumps(
            [row(d) for d in created]
            + [e for d in created for a in ("DEPLOYMENT_STARTED", "DEPLOYMENT_RUNNING",
                                            "DEPLOYMENT_COMPLETED", "DEPLOYMENT_DENIED")
               for e in audit(d, a)],
            default=str,
        )
        token = os.environ.get("DCPSECRET_JENKINS_TOKEN", "")
        check("the Jenkins token is in no deployment or audit row", bool(token) and token not in rendered)
        check("and neither is a URL", "://" not in rendered)
    finally:
        mock("/__mock/reset", {})
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM deployments WHERE application_id = :a"), {"a": application}
            )
            connection.execute(
                text("DELETE FROM deployment_targets WHERE application_id = :a"),
                {"a": application},
            )
            connection.execute(text("DELETE FROM applications WHERE id = :a"), {"a": application})
            connection.execute(text("DELETE FROM user_roles WHERE user_id = :u"), {"u": user})
            # Audit rows name the user and are append-only, so the user stays, disabled.
            connection.execute(text("UPDATE users SET status = 'DISABLED' WHERE id = :u"), {"u": user})

    print()
    print("FAILED: " + ", ".join(failures) if failures else "all deployment smoke assertions passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
