"""The AWS call layer: assumed credentials, two kinds of client, and error mapping.

Everything that talks to AWS goes through :meth:`AwsApi.call`, so the rules live in one place
(``OPEN-79``, ``OPEN-82``):

* **No static keys.** Identity is the process's ambient role; the execution role is reached by
  ``sts:AssumeRole`` with a session name that carries the operation id (so CloudTrail says which
  operation). :func:`static_credentials_present` is checked by the factory.
* **A write client cannot retry.** ``SendCommand`` is built with ``max_attempts = 0``: a timeout
  after AWS accepted the command, retried by botocore, would run the action twice. Reads use
  ``adaptive`` retries.
* **One forced re-assume** on an authentication failure (SECURITY.md §9), never more.
* No ``endpoint_url`` is accepted anywhere that production code reaches. The one test seam is the
  private ``_build_client`` argument below, used to prove the retry behaviour against a local
  server, and nothing in ``app/`` passes it.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.providers.aws.errors import map_error
from app.providers.errors import AuthenticationError, NotConfigured

ROLE_ARN: Final = re.compile(r"arn:aws:iam::(?P<account>[0-9]{12}):role/[A-Za-z0-9+=,.@_/-]{1,512}")
REGION: Final = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]")
#: Seconds. A working default, not a measurement: the minimum AWS allows is 900.
SESSION_SECONDS: Final = 900
#: Refresh this long before a cached session expires.
REFRESH_MARGIN_SECONDS: Final = 120
READ_SESSION_NAME: Final = "dcp-read"
_STATIC_KEY_VARIABLES: Final = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def static_credentials_present() -> bool:
    return any(os.environ.get(name) for name in _STATIC_KEY_VARIABLES)


@dataclass(frozen=True)
class Credentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expires_at: datetime

    def __repr__(self) -> str:  # never let a session credential reach a log line
        return "Credentials(<redacted>)"


@dataclass(frozen=True)
class Target:
    """The account and region a call is made in, from stored rows only."""

    role_arn: str
    region: str
    account_id: str


def target_from(
    *, role_arn: str | None, region: str | None, account_reference: str | None
) -> Target:
    """Validate the stored facts a call depends on. A gap is ``NotConfigured``, never a guess."""
    if role_arn is None:
        raise NotConfigured(adapter_id="aws_ssm", detail_code="credential_reference_missing")
    match = ROLE_ARN.fullmatch(role_arn)
    if match is None:
        raise NotConfigured(adapter_id="aws_ssm", detail_code="credential_reference_invalid")
    if account_reference is not None and match.group("account") != account_reference:
        raise NotConfigured(adapter_id="aws_ssm", detail_code="credential_account_mismatch")
    if region is None:
        raise NotConfigured(adapter_id="aws_ssm", detail_code="region_missing")
    if REGION.fullmatch(region) is None:
        raise NotConfigured(adapter_id="aws_ssm", detail_code="region_invalid")
    return Target(role_arn, region, match.group("account"))


ClientFactory = Callable[[str, str, "Credentials | None", bool], Any]


def _build_client(
    service: str,
    region: str,
    credentials: Credentials | None,
    write: bool,
    *,
    endpoint_url: str | None = None,
) -> Any:
    config = Config(
        region_name=region,
        connect_timeout=3,
        read_timeout=10,
        retries={"max_attempts": 0} if write else {"max_attempts": 5, "mode": "adaptive"},
        user_agent_extra="dcp",
    )
    if credentials is None:
        return boto3.client(service, config=config, endpoint_url=endpoint_url)
    return boto3.client(
        service,
        config=config,
        endpoint_url=endpoint_url,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
    )


def boto_client_factory(
    service: str, region: str, credentials: Credentials | None, write: bool
) -> Any:
    return _build_client(service, region, credentials, write)


class AwsApi:
    def __init__(
        self,
        client_factory: ClientFactory = boto_client_factory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._factory = client_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str, str], Credentials] = {}

    # -- credentials ------------------------------------------------------------------------

    def _assume(self, target: Target, session_name: str) -> Credentials:
        sts = self._factory("sts", target.region, None, False)
        try:
            response = sts.assume_role(
                RoleArn=target.role_arn,
                RoleSessionName=session_name,
                DurationSeconds=SESSION_SECONDS,
            )
        except (ClientError, BotoCoreError) as error:
            raise map_error(error, call="assume_role") from error
        raw = response["Credentials"]
        return Credentials(
            raw["AccessKeyId"], raw["SecretAccessKey"], raw["SessionToken"], raw["Expiration"]
        )

    def credentials(
        self, target: Target, session_name: str, *, refresh: bool = False
    ) -> Credentials:
        """Session credentials for the role, cached per session name until near expiry.

        The shared ``dcp-read`` session serves reads; an operation's own ``dcp-<id>`` session
        is reused across that one operation's calls (a throttled retry must not assume again)
        and pruned once expired.
        """
        key = (target.role_arn, target.region, session_name)
        with self._lock:
            now = self._clock()
            for stale in [
                k
                for k, v in self._cache.items()
                if (v.expires_at - now).total_seconds() <= REFRESH_MARGIN_SECONDS
            ]:
                del self._cache[stale]
            cached = None if refresh else self._cache.get(key)
            if cached is not None:
                return cached
            fresh = self._assume(target, session_name)
            self._cache[key] = fresh
            return fresh

    def forget(self, target: Target, session_name: str) -> None:
        with self._lock:
            self._cache.pop((target.role_arn, target.region, session_name), None)

    # -- calls ------------------------------------------------------------------------------

    def call(
        self,
        target: Target,
        service: str,
        method: str,
        *,
        call: str,
        session_name: str = READ_SESSION_NAME,
        write: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """One AWS call as the execution role. Raises a :class:`ProviderError`, nothing else.

        An authentication failure re-assumes once and repeats the call: AWS refused before
        acting, so repeating it cannot run anything twice.
        """
        for attempt in (0, 1):
            credentials = self.credentials(target, session_name, refresh=attempt == 1)
            client = self._factory(service, target.region, credentials, write)
            try:
                response: dict[str, Any] = getattr(client, method)(**kwargs)
                return response
            except (ClientError, BotoCoreError) as error:
                mapped = map_error(error, call=call)
                if isinstance(mapped, AuthenticationError) and attempt == 0:
                    self.forget(target, session_name)
                    continue
                raise mapped from error
        raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


def monotonic() -> float:
    return time.monotonic()
