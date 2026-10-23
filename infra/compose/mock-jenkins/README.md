# mock-jenkins

**A development aid, not Jenkins.** It answers the handful of REST calls DCP's Jenkins client makes
(`/crumbIssuer`, `buildWithParameters`, queue item, build status, progressive console) so the
deployment code can be exercised without a Jenkins. Passing against it is **not evidence that a real
Jenkins works**: crumb rules, queue-item expiry and plugin-specific build parameters differ by Jenkins
version and plugin set (CLAUDE.md section 7, `docs/TESTING.md`).

- Standard library only; one WSGI callable (`MockJenkins`), served with `wsgiref` in the `mocks`
  Compose profile and mounted on `httpx.WSGITransport` in the unit tests.
- Credentials: `MOCK_JENKINS_USER` / `MOCK_JENKINS_TOKEN` (defaults `dcp` / `mock-jenkins-token`;
  Compose passes `DCP_JENKINS_USER` / `DCPSECRET_JENKINS_TOKEN` when set).
- Known jobs: `demo/hello-pipeline`, `demo/hello-rollback`. Any other job is `404`.
- Control (only here, no real Jenkins has them): `POST /__mock/mode` `{"mode": ..., "crumb": ...,
  "delay": ...}`, `GET /__mock/requests`, `POST /__mock/reset`. Modes: `ok`, `outage`, `slow`,
  `queue_cancelled`, `build_failure`, `lose_queue_item`, `redirect`. Crumb: `off`, `issue`, `enforce`.
- The console contains a planted fake secret (`DEPLOY_PASSWORD=mock-canary-jenkins-0001`) so the
  log-redaction test (M5.3) has something to fail on.
