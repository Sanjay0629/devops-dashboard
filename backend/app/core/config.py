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

    # --- tunables ------------------------------------------------------------
    database_statement_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    # NoDecode: without it pydantic-settings tries to JSON-parse the env value
    # and fails before the validator below ever runs.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, because that is what env vars carry."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
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
        if self.environment != "local":
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
