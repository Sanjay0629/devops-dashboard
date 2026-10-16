"""Application configuration.

Rules enforced here (docs/SECURITY.md §9, docs/TECH_STACK.md §7):

* Credential-bearing settings have **no default** — the process refuses to start
  without them rather than falling back to something that looks like it works.
* Credential-bearing settings are ``SecretStr``, so they cannot be rendered into
  a log line, a traceback or an error message by accident.
* Production-hostile combinations (demonstration mode, wildcard CORS) are
  rejected for non-local environments at load time.

OPEN-06 (which secret store is canonical) is unresolved. The working default is
that secrets arrive as environment variables, injected by the deployment
platform. A pluggable resolver is deliberately *not* built yet: nothing in this
milestone has a provider to read from, and an interface with one unused
implementation is scaffolding. It lands with the first real provider (M4.x).
"""

from __future__ import annotations

import functools
import os
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

Environment = Literal["local", "development", "staging", "production"]


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or internally inconsistent."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DCP_",
        env_file=None,  # the platform (or Compose) injects the environment; no implicit .env
        extra="forbid",
        frozen=True,
    )

    # --- deployment identity -------------------------------------------------
    environment: Environment = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_base_path: str = "/api/v1"

    # --- demonstration mode (AD-15) -----------------------------------------
    # Defaults to False so a real deployment can never *accidentally* serve
    # synthetic data. Local development opts in explicitly via .env.
    demo_mode: bool = False

    # --- credentials: required, no defaults, never printable -----------------
    database_url: SecretStr = Field(
        ...,
        description="postgresql+psycopg://user:password@host:5432/dbname",
    )
    redis_url: SecretStr = Field(..., description="redis://host:6379/0")

    # --- OIDC (M1.3, docs/SECURITY.md §2) -----------------------------------
    # All optional: PRD §11 requires the app to start without production
    # credentials. Unconfigured, /auth/login returns 503 not_configured rather
    # than pretending to work (CLAUDE.md §7). That is also fail-closed: nobody
    # authenticates against an IdP that was never configured.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    #: Registered with the IdP **exactly** (docs/SECURITY.md §2). Not derived
    #: from the request: a Host header is attacker-controlled, and deriving a
    #: redirect URI from one is how an open redirect gets built by accident.
    oidc_redirect_url: str | None = None
    oidc_scopes: Annotated[tuple[str, ...], NoDecode] = ("openid", "email", "profile")
    #: Clock-skew tolerance on exp/iat/nbf. Bounded: a large leeway turns an
    #: expired token into a valid one.
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    oidc_http_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    #: How long a cached discovery document and JWKS are trusted before refetch.
    oidc_metadata_ttl_seconds: int = Field(default=3_600, ge=60, le=86_400)
    #: Floor between JWKS refetches triggered by an unknown `kid`, so a stream
    #: of forged kids cannot turn into a request amplifier against the IdP
    #: (docs/TESTING.md §9).
    oidc_jwks_min_refresh_seconds: int = Field(default=60, ge=5, le=3_600)

    # --- sessions (docs/SECURITY.md §5) -------------------------------------
    # Redis-only (OPEN-04, resolved: the documented default). Login and logout
    # are still audited in PostgreSQL, so the authentication record survives a
    # flushed Redis even though the live sessions do not.
    session_idle_timeout_seconds: int = Field(default=1_800, ge=60, le=86_400)
    session_absolute_timeout_seconds: int = Field(default=28_800, ge=300, le=604_800)
    #: TTL of the state/nonce/PKCE record created by /auth/login.
    oidc_login_state_ttl_seconds: int = Field(default=600, ge=60, le=3_600)

    # --- rate limiting (AD-19, docs/SECURITY.md §13) ------------------------
    auth_rate_limit_max: int = Field(default=10, ge=1, le=10_000)
    auth_rate_limit_window_seconds: int = Field(default=300, ge=1, le=86_400)
    #: The read class: 600/min per identity (docs/SECURITY.md §13), which is
    #: polling headroom rather than a meter. Fails *open*, unlike the auth
    #: class — see ``app/auth/dependencies.require_read_budget``.
    read_rate_limit_max: int = Field(default=600, ge=1, le=100_000)
    read_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)

    # --- Prometheus (M2.1, AD-04, docs/SECURITY.md §19) ---------------------
    #: Base URL of the Prometheus **read** API, e.g. ``http://prometheus:9090``.
    #: Optional: unset, every metrics read reports ``not_configured`` rather
    #: than pretending to have a source (PRD §11, CLAUDE.md §7).
    #:
    #: This is **deployment configuration, not a registered destination**, so
    #: it deliberately does not pass through
    #: ``app/core/ssrf.validate_registered_url``. The reasoning is written out
    #: in ``app/metrics/client.py`` — in short, the host allowlist and the
    #: denied-range check exist to constrain destinations that a *user*
    #: supplies through the API, and applying them here would both
    #: self-authorise (whoever sets this variable sets the allowlist too) and
    #: deny the only addresses an internal monitoring host can have.
    prometheus_url: str | None = None
    #: Upstream deadline for one Prometheus call (docs/API_CONTRACTS.md §15.2,
    #: docs/ARCHITECTURE.md §4.4 "default 10 s"). Also sent to Prometheus as
    #: its own ``timeout`` parameter, so a slow query is abandoned at both
    #: ends rather than only at ours.
    prometheus_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    # --- Target discovery and agent health (M2.3, OPEN-14) -------------------
    #: Where DCP writes the Prometheus ``file_sd`` target file. Unset means
    #: discovery is **off**: nothing is written and the regeneration task says
    #: so, rather than pretending a file exists. In Compose this is a path on a
    #: volume the worker writes and Prometheus reads read-only.
    metrics_targets_file: str | None = None
    #: Ports a ``metrics_instance`` may name. Prometheus connects to whatever is
    #: in the target file, so an administrator-supplied ``host:port`` is an
    #: outbound connection to an arbitrary port; restricting it to the exporter
    #: port(s) keeps that from being a port scanner. Comma-separated.
    metrics_target_ports: Annotated[tuple[int, ...], NoDecode] = (9100,)
    #: How often the agent probe reads ``up`` from Prometheus.
    agent_probe_interval_seconds: int = Field(default=30, ge=10, le=3_600)
    #: An exporter whose every scrape failed for this long is ``UNREACHABLE``
    #: (``servers.agent_status``). Must span at least two scrapes at the
    #: stack's 15 s interval, or one lost scrape would flip a healthy host.
    agent_unreachable_after_seconds: int = Field(default=60, ge=30, le=3_600)

    # --- SSRF (docs/SECURITY.md §19) ----------------------------------------
    #: Hosts a registered URL may name. **Empty means none**, so an
    #: unconfigured deployment registers no outbound destination at all rather
    #: than defaulting to "anywhere" (mandatory rule 6, mandatory rule 9).
    #: Exact hostnames only; no wildcards and no patterns, because a pattern
    #: here is how ``evil-example.com.attacker.net`` gets allowed by accident.
    ssrf_host_allowlist: Annotated[tuple[str, ...], NoDecode] = ()
    #: Hosts for which plain ``http`` is accepted, a subset of the allowlist
    #: (§19 rule 3). Normally empty.
    ssrf_insecure_hosts: Annotated[tuple[str, ...], NoDecode] = ()

    # --- tunables ------------------------------------------------------------
    database_statement_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    #: How long after ``servers.last_seen_at`` a host counts as stale, for the
    #: computed ``stale`` flag on server detail (docs/API_CONTRACTS.md §14.3).
    #: One global value: AD-14 gives the per-environment thresholds and the
    #: sweep that recomputes ``status`` to M2.5, and inventing them here would
    #: pre-empt that milestone.
    server_stale_after_seconds: int = Field(default=300, ge=30, le=86_400)
    #: How long a dashboard aggregate may be served from Redis (§13).
    #: Short by design: the dashboard is a polled view, so the cache exists to
    #: absorb a refresh interval rather than to hold data. ``0`` disables
    #: caching entirely, which is correct and slower — the documented rollback
    #: for M1.6 (IMPLEMENTATION_PLAN.md).
    dashboard_cache_ttl_seconds: int = Field(default=15, ge=0, le=3_600)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    # NoDecode: without it pydantic-settings tries to JSON-parse the env value
    # and fails before the validator below ever runs.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("cors_origins", "ssrf_host_allowlist", "ssrf_insecure_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, because that is what env vars carry."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("metrics_target_ports", mode="before")
    @classmethod
    def _split_ports(cls, value: object) -> object:
        """Comma-separated ports, each a valid TCP port; at least one."""
        if isinstance(value, str):
            value = tuple(int(part) for part in value.split(",") if part.strip())
        if not isinstance(value, tuple) or not value:
            raise ValueError("must name at least one port")
        if any(not 1 <= port <= 65_535 for port in value):
            raise ValueError("every port must be between 1 and 65535")
        return value

    @field_validator("oidc_scopes", mode="before")
    @classmethod
    def _split_scopes(cls, value: object) -> object:
        """Accept a space- or comma-separated string, as OIDC and env vars do."""
        if isinstance(value, str):
            return tuple(part for part in value.replace(",", " ").split() if part)
        return value

    @field_validator("oidc_issuer", "oidc_redirect_url", "prometheus_url")
    @classmethod
    def _check_absolute_http_url(cls, value: str | None) -> str | None:
        """Reject anything that is not a plain absolute http(s) URL.

        A relative or non-HTTP value here would be fetched or redirected to
        later; rejecting it at load time keeps that out of the request path.

        The credential, fragment and whitespace checks are the same three
        ``app/core/ssrf.py`` applies to a registered URL, for the same
        reasons: an embedded credential ends up in whatever logs the URL, a
        fragment is never sent and so means the value was pasted from a
        browser, and an embedded newline is header injection waiting for a
        client that splits on it. They are enforced here rather than only
        there because these three settings are fetched (or redirected to) and
        do **not** go through the registered-URL path.
        """
        if value is None:
            return None
        if any(character in value for character in "\r\n\t "):
            raise ValueError("must be a single-line URL with no whitespace")
        value = value.rstrip("/") or value
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an absolute http:// or https:// URL")
        parts = urlsplit(value)
        if parts.username or parts.password:
            raise ValueError("must not embed credentials")
        if parts.fragment:
            raise ValueError("must not carry a fragment")
        if not parts.hostname:
            raise ValueError("must name a host")
        return value

    @field_validator("api_base_path")
    @classmethod
    def _check_base_path(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("must start with '/' and must not end with '/'")
        return value

    @model_validator(mode="after")
    def _reject_unsafe_combinations(self) -> Settings:
        if "*" in self.cors_origins:
            # docs/SECURITY.md §12 — never a wildcard, in any environment.
            raise ValueError("cors_origins must not contain '*'")
        if "*" in self.ssrf_host_allowlist:
            # Same rule for the same reason: an allowlist with a wildcard is
            # not an allowlist (docs/SECURITY.md §19).
            raise ValueError("ssrf_host_allowlist must not contain '*'")
        if not set(self.ssrf_insecure_hosts) <= set(self.ssrf_host_allowlist):
            raise ValueError("ssrf_insecure_hosts must be a subset of ssrf_host_allowlist")
        if "openid" not in self.oidc_scopes:
            # Without it the provider need not issue an ID token at all, and
            # ID-token verification is the whole authentication decision.
            raise ValueError("oidc_scopes must include 'openid'")
        if self.session_idle_timeout_seconds > self.session_absolute_timeout_seconds:
            raise ValueError(
                "session_idle_timeout_seconds must not exceed "
                "session_absolute_timeout_seconds; an idle deadline beyond the "
                "absolute one can never be reached"
            )
        if self.environment != "local":
            # ``prometheus_url`` is included deliberately. docs/SECURITY.md
            # §19.3 allows plain http only for an explicitly allowlisted
            # internal host, and this setting is not allowlist-gated (see the
            # field comment), so the remaining honest rule is: https outside
            # 'local'. An internal Prometheus without TLS has to be fronted
            # by something that has it.
            for name in ("oidc_issuer", "oidc_redirect_url", "prometheus_url"):
                url = getattr(self, name)
                if url is not None and not url.startswith("https://"):
                    raise ValueError(f"{name} must use https outside the 'local' environment")
            if self.demo_mode:
                raise ValueError(
                    "demo_mode must be false outside the 'local' environment; "
                    "serving synthetic data from a real deployment is prohibited"
                )
            if self.log_level == "DEBUG":
                raise ValueError("log_level DEBUG is not permitted outside 'local'")
        return self

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def oidc_configured(self) -> bool:
        return all(
            value is not None
            for value in (
                self.oidc_issuer,
                self.oidc_client_id,
                self.oidc_client_secret,
                self.oidc_redirect_url,
            )
        )

    @property
    def prometheus_configured(self) -> bool:
        return self.prometheus_url is not None

    @property
    def cookie_secure(self) -> bool:
        """``Secure`` on the session and CSRF cookies.

        Derived, not configurable. docs/SECURITY.md §5 permits exactly one
        deviation — ``Secure=false`` on localhost — so this is a property
        rather than a setting that a deployment could get wrong.
        """
        return not self.is_local


def _unknown_environment_variables() -> list[str]:
    """Find DCP_-prefixed variables that match no setting.

    pydantic-settings reads only the variables it knows about, so a typo like
    ``DCP_DEMOMODE`` is silently ignored. In a tool that controls production
    services, a config option that looks set but is not is worth failing over.
    """
    known = {f"DCP_{name.upper()}" for name in Settings.model_fields}
    return sorted(name for name in os.environ if name.startswith("DCP_") and name not in known)


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        variable = f"DCP_{location.upper()}" if location != "<root>" else "<root>"
        lines.append(f"  - {variable}: {item['msg']}")
    return "\n".join(lines)


def load_settings() -> Settings:
    """Load settings, failing immediately and legibly when configuration is bad.

    Deliberately does not log the offending values — a bad ``DCP_DATABASE_URL``
    still contains a password.
    """
    unknown = _unknown_environment_variables()
    if unknown:
        raise ConfigurationError(
            "Unrecognised DCP_* environment variable(s); check for a typo:\n"
            + "\n".join(f"  - {name}" for name in unknown)
        )
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as error:
        raise ConfigurationError(
            "Invalid or incomplete configuration. See .env.example for the full list.\n"
            + _format_validation_error(error)
        ) from error
    except SettingsError as error:
        # Raised by a settings source *before* field validation, e.g. a value
        # that cannot be parsed into a complex type. Message only, never the
        # value — the value may be a credential.
        raise ConfigurationError(f"Could not read configuration: {error}") from error


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache via ``get_settings.cache_clear()``."""
    return load_settings()
