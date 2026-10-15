#!/usr/bin/env python
"""`.env.example` completeness check (IMPLEMENTATION_PLAN.md M1.7).

The acceptance criterion for M1.7 is that a new developer can go from clone to
a working stack by following the README alone. `.env.example` is the only
place that tells them which variables exist, so a setting that is live in the
application but missing here is a gap in that path — and an undocumented
variable is the kind of thing nobody notices until a deployment is missing it.

Run from `backend/` so the Settings model can be imported rather than parsed:

    cd backend && uv run python ../scripts/check_env_example.py

Importing is the point. A regex over `config.py` would drift from the model
the moment a field moved, and this check exists precisely to catch drift.

Four rules:

1.  Every `DCP_*` field on `Settings` appears in `.env.example`. These are
    live settings; an operator cannot configure what is not written down.
2.  Every `DCP_*` key in `.env.example` is a real field. An unrecognised
    `DCP_*` variable is a startup error by design (`extra="forbid"`), so a
    typo here hands someone a file that refuses to boot. This shifts that
    failure left.
3.  Every variable the **core** and **mocks** Compose profiles substitute is
    documented. Profiles whose services are still placeholders are exempt and
    named in the output, because `.env.example` deliberately withholds
    variables for unimplemented integrations: an unused variable there would
    imply a working integration (CLAUDE.md §7).
4.  The test-harness variables are documented. They are not `DCP_`-prefixed on
    purpose, so rule 1 cannot see them, and a developer who misses them gets a
    silently skipped integration suite.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "infra" / "compose" / "docker-compose.yml"

#: Profiles whose services exist today. A variable referenced only by a
#: placeholder profile is not a documentation gap yet; the milestone that
#: implements the service documents it, together with the integration.
IMPLEMENTED_PROFILES = {"core", "mocks"}

#: Not `DCP_`-prefixed deliberately (that prefix is reserved for application
#: settings and an unknown one is a startup error), so rule 1 cannot reach
#: them. Both are read by `scripts/check-all.sh` and by the CI backend job;
#: without them the integration suite skips while the summary still reads
#: green, which is the failure this rule exists to prevent.
REQUIRED_HARNESS_VARS = ("TEST_DATABASE_URL", "TEST_REDIS_URL")

VARIABLE_REFERENCE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")


def documented_keys(text: str) -> set[str]:
    """Assignments in a dotenv file, ignoring comments and blank lines.

    A commented-out assignment does not count as documented: the developer
    copying this file gets no value from it, and `${VAR:-default}` in Compose
    would then quietly win.
    """
    keys = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def compose_variables() -> tuple[set[str], dict[str, set[str]]]:
    """Variables substituted by implemented profiles, and those deferred.

    Parsed rather than regexed over the whole file so that a variable can be
    attributed to the service that uses it — which is what makes the
    profile exemption honest instead of a blanket ignore.
    """
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services: dict[str, object] = document.get("services", {})

    required: set[str] = set()
    deferred: dict[str, set[str]] = {}
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        profiles = set(service.get("profiles") or [])
        # The YAML anchor for the shared backend environment is resolved by
        # the parser, so dumping the service subtree sees those `${...}`
        # references too.
        referenced = set(VARIABLE_REFERENCE.findall(yaml.safe_dump(service)))
        if profiles & IMPLEMENTED_PROFILES or not profiles:
            required |= referenced
        else:
            for variable in referenced:
                deferred.setdefault(variable, set()).update(profiles)
    # A variable used by an implemented profile is required even if some
    # placeholder also mentions it.
    for variable in required:
        deferred.pop(variable, None)
    return required, deferred


def settings_fields() -> set[str]:
    try:
        from app.core.config import Settings
    except ImportError as error:  # pragma: no cover - environment problem
        print(
            "Could not import the Settings model. Run this from `backend/`:\n"
            "    cd backend && uv run python ../scripts/check_env_example.py\n"
            f"({error})",
            file=sys.stderr,
        )
        raise SystemExit(2) from error
    prefix = Settings.model_config.get("env_prefix", "")
    return {f"{prefix}{name}".upper() for name in Settings.model_fields}


def main() -> int:
    documented = documented_keys(ENV_EXAMPLE.read_text(encoding="utf-8"))
    fields = settings_fields()
    required_by_compose, deferred = compose_variables()

    problems: list[str] = []

    missing_settings = sorted(fields - documented)
    if missing_settings:
        problems.append(
            "These settings exist on the Settings model but are not in "
            ".env.example, so nobody reading it knows they can be set:\n  "
            + "\n  ".join(missing_settings)
        )

    unknown = sorted(key for key in documented if key.startswith("DCP_") and key not in fields)
    if unknown:
        problems.append(
            "These DCP_* keys in .env.example match no Settings field. An "
            "unrecognised DCP_* variable is a startup error, so copying this "
            "file would produce an application that refuses to boot:\n  "
            + "\n  ".join(unknown)
        )

    missing_compose = sorted(required_by_compose - documented - fields)
    if missing_compose:
        problems.append(
            "These variables are substituted by an implemented Compose "
            "profile but are not documented in .env.example:\n  "
            + "\n  ".join(missing_compose)
        )

    missing_harness = sorted(set(REQUIRED_HARNESS_VARS) - documented)
    if missing_harness:
        problems.append(
            "These test-harness variables are not documented. Without them "
            "the integration suite skips while the summary still reads "
            "green:\n  " + "\n  ".join(missing_harness)
        )

    print(f"{len(documented)} keys documented in .env.example")
    print(f"{len(fields)} DCP_* settings on the model, {len(required_by_compose)} compose vars")
    if deferred:
        print("deferred to the milestone that implements the service:")
        for variable, profiles in sorted(deferred.items()):
            print(f"  {variable} (profiles: {', '.join(sorted(profiles))})")

    if problems:
        print()
        for problem in problems:
            print(f"PROBLEM: {problem}\n", file=sys.stderr)
        print(f"{len(problems)} problem group(s) in .env.example", file=sys.stderr)
        return 1

    print(".env.example is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
