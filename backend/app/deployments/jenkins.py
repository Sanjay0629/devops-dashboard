"""The Jenkins REST client (M5.1; ``OPEN-119``, ``OPEN-125``; TECH_STACK.md §6.5).

Scope: trigger a registered job, resolve its queue item to a build, read a build's status, and
read console text. **Nothing here takes a URL, a header or a command from a caller.** The base URL,
user and token reference are deployment configuration (``DCP_JENKINS_*``); the only caller-supplied
values are a validated job path (turned into ``/job/<seg>/job/<seg>`` by
``app.deployments.paths``), integers, and the build-parameter mapping that
``app.deployments.template.render_parameters`` produced.

How each rule is kept:

* **Allowlist and checked address, on every call.** The URL goes through
  ``ssrf.pin_to_checked_address``: re-validated against the host allowlist, resolved once, every
  address checked, and the connection made to the checked address. No redirect, no proxy.
* **The token** is resolved per request from the secret store, put in a ``Basic`` header at the
  call site, and never stored on the client, logged, or placed in an error.
* **Reads are retried, a trigger never is.** A read is retried at most twice with backoff on a
  connect failure, a read timeout, ``502/503/504`` or ``429``. The trigger is one attempt: if
  Jenkins may have acted, the error says so (``may_have_acted``) and the caller reconciles from the
  persisted handle rather than triggering again (CLAUDE.md §5).
* **A response body is never returned or logged in an error.** A failure is a closed ``reason``.
* **The ``Location`` header is data.** Only a numeric queue id is parsed out of it; it is never
  followed or stored as a URL.
* **Circuit breaker:** the generic class from ``app.metrics.client``, its own instance. It counts
  availability failures only; a ``401``, ``403`` or ``404`` is Jenkins *answering* and counts as a
  success for the breaker and a specific error for the caller.

``ConsoleChunk.text`` is **raw**. Redaction is the service layer's job (M5.3).
"""

from __future__ import annotations

import base64
import json
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import httpx

from app.core import secrets
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotConfiguredError
from app.core.logging import get_logger
from app.core.ssrf import OutboundError, Resolver, UrlNotAllowedError, pin_to_checked_address
from app.deployments.paths import job_url_path
from app.metrics.client import CircuitBreaker

logger = get_logger(__name__)

JenkinsReason = Literal[
    "not_configured",
    "provider_unreachable",
    "provider_timeout",
    "provider_error",
    "circuit_open",
    "auth_failed",
    "job_not_found",
    "crumb_rejected",
    "trigger_outcome_unknown",
    "destination_refused",
]

CONNECT_TIMEOUT_SECONDS: Final = 3.0
READ_RETRIES: Final = 2
BACKOFF_SECONDS: Final = (0.5, 1.0)
RETRY_AFTER_CAP_SECONDS: Final = 5.0
JSON_LIMIT_BYTES: Final = 1024 * 1024
CONSOLE_LIMIT_BYTES: Final = 256 * 1024

_RETRYABLE_STATUS: Final = frozenset({429, 502, 503, 504})
_QUEUE_ITEM: Final = re.compile(r"/queue/item/(\d{1,12})/?")
_PARAMETER_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_PARAMETER_VALUE: Final = re.compile(r"[A-Za-z0-9-]{1,64}")
#: How many recent builds ``find_by_parameter`` looks through. A build older than this is not
#: found, which is the honest answer (``None``) and not a guess.
RECENT_BUILDS: Final = 50
_CRUMB_FIELD: Final = re.compile(r"[A-Za-z0-9-]{1,64}")
_CRUMB_VALUE: Final = re.compile(r"[A-Za-z0-9._:+=/-]{1,512}")
_SHA: Final = re.compile(r"[0-9a-f]{40}")
_RESULTS: Final = frozenset({"SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT"})

#: How each reason reaches an API client. A reason is never a sentence from Jenkins.
API_STATUS: Final[dict[str, tuple[int, str]]] = {
    "not_configured": (503, "not_configured"),
    "provider_unreachable": (503, "provider_unavailable"),
    "provider_timeout": (504, "provider_timeout"),
    "provider_error": (502, "provider_error"),
    "circuit_open": (503, "provider_unavailable"),
    "auth_failed": (502, "provider_error"),
    "job_not_found": (502, "provider_error"),
    "crumb_rejected": (502, "provider_error"),
    "trigger_outcome_unknown": (502, "provider_error"),
    "destination_refused": (503, "provider_unavailable"),
}
#: The failures that say Jenkins (or the path to it) is unhealthy; the circuit counts these.
AVAILABILITY: Final = frozenset({"provider_unreachable", "provider_timeout", "provider_error"})


class JenkinsError(Exception):
    """A Jenkins call did not complete. ``reason`` is closed; the message never carries a body.

    ``may_have_acted`` is ``True`` only when the request went out and Jenkins' answer is missing or
    ambiguous (a timeout or reset after the write, a ``5xx``). A request that never left, or one
    Jenkins refused with a ``4xx``, has it ``False``: it definitely did not queue anything.
    """

    def __init__(self, reason: JenkinsReason, *, may_have_acted: bool = False) -> None:
        super().__init__(reason)
        self.reason: JenkinsReason = reason
        self.may_have_acted = may_have_acted


def to_app_error(error: JenkinsError) -> AppError:
    """The API's view of ``error`` (API_CONTRACTS §3). Never carries Jenkins' words."""
    if error.reason == "not_configured":
        return NotConfiguredError("Jenkins is not configured.", details={"reason": error.reason})
    status, code = API_STATUS[error.reason]
    app_error = AppError(
        "Jenkins could not complete the request.", details={"reason": error.reason}
    )
    app_error.status_code, app_error.code = status, code
    return app_error


@dataclass(frozen=True, slots=True)
class QueueRef:
    queue_id: int


@dataclass(frozen=True, slots=True)
class QueueState:
    #: ``gone`` is a ``404``: Jenkins forgets a queue item some minutes after it starts, so "not
    #: found" is *not* "never existed" -- the caller reconciles rather than re-triggering.
    kind: Literal["waiting", "cancelled", "started", "gone"]
    build_number: int | None = None


@dataclass(frozen=True, slots=True)
class BuildState:
    building: bool
    #: ``None`` while building (or when Jenkins did not say) -- not a guess.
    result: str | None
    started_at: datetime | None
    duration_ms: int | None
    commit_sha: str | None


@dataclass(frozen=True, slots=True)
class Located:
    """What :meth:`JenkinsClient.find_by_parameter` found: a queue item or a started build."""

    kind: Literal["queue", "build"]
    #: The queue item id, or the build number.
    number: int


@dataclass(frozen=True, slots=True)
class ConsoleChunk:
    """Raw console text from ``offset``. Not redacted."""

    text: str
    next_offset: int
    more: bool


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    headers: httpx.Headers
    body: bytes
    truncated: bool


_BREAKER: Final[CircuitBreaker] = CircuitBreaker()


def breaker() -> CircuitBreaker:
    return _BREAKER


def reset_breaker() -> None:
    _BREAKER.reset()


class JenkinsClient:
    """``transport``, ``resolver``, ``sleep`` and ``circuit`` exist for tests only."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
        circuit: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        secret_backend: secrets.SecretBackend | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._transport = transport
        self._resolver = resolver
        self.circuit = circuit or breaker()
        self._sleep = sleep
        self._secret_backend = secret_backend

    def __repr__(self) -> str:
        return "<JenkinsClient>"

    @property
    def configured(self) -> bool:
        return self._settings.jenkins_url is not None

    # -- public operations ---------------------------------------------------------------

    def probe(self) -> datetime:
        """Cheapest authenticated read that proves Jenkins can be reached and parsed (§26.1)."""
        started = datetime.now(UTC)
        data = self._json(self._call("GET", "/api/json", params={"tree": "mode"}))
        if not isinstance(data, dict):
            raise JenkinsError("provider_error")
        return started

    def trigger(self, job_path: str, parameters: Mapping[str, str]) -> QueueRef:
        """Queue one build. **One attempt, never retried.**

        ``parameters`` is the output of ``render_parameters``: this method adds nothing to it and
        takes nothing else from the caller. Raises :class:`JenkinsError`; when its
        ``may_have_acted`` is true the outcome is unknown and the reason is
        ``trigger_outcome_unknown`` -- reconcile, do not trigger again.
        """
        path = f"{job_url_path(job_path)}/buildWithParameters"
        headers = self._crumb_headers()
        try:
            reply = self._call(
                "POST", path, data=dict(parameters), headers=headers, retries=0, post=True
            )
        except JenkinsError as error:
            if error.may_have_acted:
                raise JenkinsError("trigger_outcome_unknown", may_have_acted=True) from error
            raise
        match = _QUEUE_ITEM.search(urlsplit(reply.headers.get("location", "")).path)
        if reply.status != 201 or match is None:
            # Accepted, but not in a shape that can be tracked: it may have queued something.
            raise JenkinsError("trigger_outcome_unknown", may_have_acted=True)
        return QueueRef(int(match.group(1)))

    def queue_item(self, queue_id: int) -> QueueState:
        if not isinstance(queue_id, int) or isinstance(queue_id, bool) or queue_id < 1:
            raise ValueError("queue_id must be a positive integer")
        try:
            data = self._json(self._call("GET", f"/queue/item/{queue_id}/api/json"))
        except JenkinsError as error:
            if error.reason == "job_not_found":
                return QueueState("gone")
            raise
        if not isinstance(data, dict):
            raise JenkinsError("provider_error")
        if data.get("cancelled") is True:
            return QueueState("cancelled")
        executable = data.get("executable")
        if isinstance(executable, dict):
            number = executable.get("number")
            if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
                return QueueState("started", number)
            raise JenkinsError("provider_error")
        return QueueState("waiting")

    def build(self, job_path: str, number: int) -> BuildState:
        self._number(number)
        path = f"{job_url_path(job_path)}/{number}/api/json"
        data = self._json(
            self._call(
                "GET",
                path,
                params={
                    "tree": "building,result,timestamp,duration,actions[lastBuiltRevision[SHA1]]"
                },
            )
        )
        if not isinstance(data, dict) or not isinstance(data.get("building"), bool):
            raise JenkinsError("provider_error")
        result = data.get("result")
        if result is not None and result not in _RESULTS:
            raise JenkinsError("provider_error")
        timestamp, duration = data.get("timestamp"), data.get("duration")
        return BuildState(
            building=data["building"],
            result=result,
            started_at=(
                datetime.fromtimestamp(timestamp / 1000, UTC)
                if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool)
                else None
            ),
            duration_ms=duration
            if isinstance(duration, int) and not isinstance(duration, bool)
            else None,
            commit_sha=_commit_sha(data.get("actions")),
        )

    def find_by_parameter(self, job_path: str, name: str, value: str) -> Located | None:
        """Look, **read-only**, for the one queue item or build carrying ``name=value``.

        How an unknown trigger outcome is reconciled (``OPEN-137``): ``value`` is the UUID DCP
        itself generated for the deployment, so nothing a request supplied is searched for, and
        a find is followed, never a second trigger. The queue is read **first**: an item that
        starts between the two reads is then in the build list, whereas the other order could
        miss it in both.

        ``None`` means *not found in what Jenkins showed* -- including a Jenkins that does not
        expose parameters, or a build older than :data:`RECENT_BUILDS` -- and the caller says so.
        More than one match is ``provider_error``: it means a double trigger happened, and
        guessing which build is DCP's would hide it. A garbled list is also ``provider_error``,
        never ``None``: "I could not read it" must not read as "there is no build".
        """
        if _PARAMETER_NAME.fullmatch(name) is None:
            raise ValueError("name must match [A-Za-z_][A-Za-z0-9_]{0,63}")
        if _PARAMETER_VALUE.fullmatch(value) is None:
            raise ValueError("value must match [A-Za-z0-9-]{1,64}")
        builds_path = f"{job_url_path(job_path)}/api/json"  # validates job_path before any call
        queue = self._json(
            self._call(
                "GET",
                "/queue/api/json",
                params={"tree": "items[id,actions[parameters[name,value]]]"},
            )
        )
        found = _matches(queue, "items", "id", name, value)
        if found:
            return Located("queue", found)
        builds = self._json(
            self._call(
                "GET",
                builds_path,
                params={
                    "tree": f"builds[number,actions[parameters[name,value]]]{{0,{RECENT_BUILDS}}}"
                },
            )
        )
        found = _matches(builds, "builds", "number", name, value)
        return Located("build", found) if found else None

    def console(self, job_path: str, number: int, offset: int = 0) -> ConsoleChunk:
        """Console text from byte ``offset``, at most 256 KiB, raw."""
        self._number(number)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        path = f"{job_url_path(job_path)}/{number}/logText/progressiveText"
        reply = self._call(
            "GET", path, params={"start": str(offset)}, limit=CONSOLE_LIMIT_BYTES, truncate=True
        )
        # ponytail: a cut at the byte limit can split a multi-byte character; the next chunk then
        # starts mid-character and decodes with a replacement. Cut on a character boundary if a
        # build log in a non-ASCII language ever matters.
        text = reply.body.decode("utf-8", errors="replace")
        size = reply.headers.get("x-text-size", "")
        total = int(size) if size.isdigit() else offset + len(reply.body)
        next_offset = offset + len(reply.body) if reply.truncated else total
        more = reply.truncated or reply.headers.get("x-more-data", "").lower() == "true"
        return ConsoleChunk(text, next_offset, more)

    # -- internals -----------------------------------------------------------------------

    @staticmethod
    def _number(number: int) -> None:
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError("number must be a positive integer")

    def _crumb_headers(self) -> dict[str, str]:
        try:
            reply = self._call("GET", "/crumbIssuer/api/json")
        except JenkinsError as error:
            if error.reason == "job_not_found":  # a 404: this Jenkins issues no crumb
                return {}
            raise
        data = self._json(reply)
        field = data.get("crumbRequestField") if isinstance(data, dict) else None
        value = data.get("crumb") if isinstance(data, dict) else None
        if (
            not isinstance(field, str)
            or not isinstance(value, str)
            or _CRUMB_FIELD.fullmatch(field) is None
            or _CRUMB_VALUE.fullmatch(value) is None
        ):
            raise JenkinsError("provider_error")
        return {field: value}

    @staticmethod
    def _json(reply: _Reply) -> Any:
        try:
            return json.loads(reply.body)
        except ValueError:
            raise JenkinsError("provider_error") from None

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        retries: int = READ_RETRIES,
        limit: int = JSON_LIMIT_BYTES,
        truncate: bool = False,
        post: bool = False,
    ) -> _Reply:
        """One logical call: the circuit, the retry loop, and the breaker's bookkeeping."""
        if not self.circuit.allow():
            raise JenkinsError("circuit_open")
        try:
            reply = self._with_retries(
                method, path, params, data, headers, retries, limit, truncate, post
            )
        except JenkinsError as error:
            if error.reason in AVAILABILITY:
                self.circuit.record_failure(error.reason)  # type: ignore[arg-type]
            elif error.reason in {"auth_failed", "job_not_found", "crumb_rejected"}:
                self.circuit.record_success()  # Jenkins answered
            else:
                self.circuit.release_probe()  # never reached Jenkins: neither success nor failure
            raise
        self.circuit.record_success()
        return reply

    def _with_retries(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        data: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        retries: int,
        limit: int,
        truncate: bool,
        post: bool,
    ) -> _Reply:
        attempt = 0
        while True:
            try:
                return self._attempt(method, path, params, data, headers, limit, truncate, post)
            except _RetryableError as retryable:
                if attempt >= retries:
                    raise retryable.error from None
                delay = retryable.retry_after
                if delay is None:
                    delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                self._sleep(delay + random.uniform(0, 0.1))  # noqa: S311  # nosec B311
                attempt += 1

    def _attempt(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        data: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        limit: int,
        truncate: bool,
        post: bool,
    ) -> _Reply:
        base = self._settings.jenkins_url
        reference = self._settings.jenkins_token_reference
        user = self._settings.jenkins_user
        if base is None or reference is None or user is None:
            raise JenkinsError("not_configured")
        try:
            token = secrets.resolve(reference, self._secret_backend).get_secret_value()
        except secrets.SecretUnresolvableError:
            raise JenkinsError("not_configured") from None
        try:
            pinned, pin_headers, extensions = pin_to_checked_address(
                f"{base}{path}", self._resolver
            )
        except UrlNotAllowedError:
            raise JenkinsError("destination_refused") from None
        except OutboundError as error:
            raise JenkinsError(
                "destination_refused"
                if error.reason == "destination_refused"
                else "provider_unreachable"
            ) from None

        request_headers = {
            **pin_headers,
            **(headers or {}),
            "Accept": "application/json, text/plain",
            "Authorization": "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode(),
        }
        deadline = time.monotonic() + self._settings.jenkins_timeout_seconds
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    timeout=httpx.Timeout(
                        self._settings.jenkins_timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS
                    ),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    method,
                    pinned,
                    params=dict(params) if params else None,
                    data=dict(data) if data is not None else None,
                    headers=request_headers,
                    extensions=extensions,
                ) as response,
            ):
                chunks: list[bytes] = []
                size = 0
                truncated = False
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise httpx.ReadTimeout("deadline")
                    size += len(chunk)
                    if size > limit:
                        if not truncate or response.status_code != 200:
                            raise JenkinsError("provider_error", may_have_acted=post)
                        chunks.append(chunk[: len(chunk) - (size - limit)])
                        truncated = True
                        break
                    chunks.append(chunk)
                status, reply_headers = response.status_code, response.headers
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            logger.warning("jenkins_unreachable")
            raise _RetryableError(JenkinsError("provider_unreachable")) from None
        except httpx.TimeoutException:
            logger.warning("jenkins_timed_out")
            raise _RetryableError(JenkinsError("provider_timeout", may_have_acted=post)) from None
        except httpx.HTTPError:
            logger.warning("jenkins_transport_error")
            failure = JenkinsError("provider_unreachable", may_have_acted=post)
            raise _RetryableError(failure) from None

        reply = _Reply(status, reply_headers, b"".join(chunks), truncated)
        return self._check_status(reply, post=post)

    def _check_status(self, reply: _Reply, *, post: bool) -> _Reply:
        status = reply.status
        if 200 <= status < 300:
            return reply
        logger.warning("jenkins_rejected_request", status=status)
        if status in _RETRYABLE_STATUS:
            retry_after = reply.headers.get("retry-after", "")
            delay = (
                min(float(retry_after), RETRY_AFTER_CAP_SECONDS) if retry_after.isdigit() else None
            )
            # 429 is a refusal before acting; a 5xx may or may not have been.
            error = JenkinsError("provider_error", may_have_acted=post and status != 429)
            raise _RetryableError(error, retry_after=delay)
        if status == 401:
            raise JenkinsError("auth_failed")
        if status == 403:
            body = reply.body[:2048].lower()
            if b"crumb" in body:
                raise JenkinsError("crumb_rejected")
            raise JenkinsError("auth_failed")
        if status == 404:
            raise JenkinsError("job_not_found")
        if 400 <= status < 500:
            raise JenkinsError("provider_error")  # refused: it did not act
        # 3xx (never followed) and any other 5xx.
        raise JenkinsError("provider_error", may_have_acted=post and status >= 500)


class _RetryableError(Exception):
    """Internal: carries the error to raise if the retry budget is spent."""

    def __init__(self, error: JenkinsError, *, retry_after: float | None = None) -> None:
        super().__init__(error.reason)
        self.error = error
        self.retry_after = retry_after


def _matches(data: object, listing: str, key: str, name: str, value: str) -> int | None:
    """The one entry of ``data[listing]`` whose parameters include ``name=value``, by its ``key``.

    Every entry must be a dict with a positive integer ``key`` -- a list that is not what Jenkins
    sends is ``provider_error``, not an empty answer. Entries' ``actions`` may be anything (a real
    queue item carries many kinds); what is not a parameter list is skipped.
    """
    entries = data.get(listing) if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise JenkinsError("provider_error")
    hits: list[int] = []
    for entry in entries:
        number = entry.get(key) if isinstance(entry, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise JenkinsError("provider_error")
        actions = entry.get("actions")
        for action in actions if isinstance(actions, list) else ():
            parameters = action.get("parameters") if isinstance(action, dict) else None
            for parameter in parameters if isinstance(parameters, list) else ():
                if (
                    isinstance(parameter, dict)
                    and parameter.get("name") == name
                    and parameter.get("value") == value
                ):
                    hits.append(number)
    if len(set(hits)) > 1:
        raise JenkinsError("provider_error")
    return hits[0] if hits else None


def _commit_sha(actions: object) -> str | None:
    for action in actions if isinstance(actions, list) else ():
        revision = action.get("lastBuiltRevision") if isinstance(action, dict) else None
        sha = revision.get("SHA1") if isinstance(revision, dict) else None
        if isinstance(sha, str) and _SHA.fullmatch(sha):
            return sha
    return None
