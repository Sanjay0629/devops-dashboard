"""The sample log host's identity: the seed and the Compose file say the same thing."""

from __future__ import annotations

import re
from pathlib import Path

from app.inventory import seed

COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"


def _compose_default(variable: str) -> str:
    match = re.search(rf"{variable}: \$\{{{variable}:-([0-9a-f-]+)\}}", COMPOSE.read_text("utf-8"))
    assert match, f"{variable} has no default in the Compose file"
    return match.group(1)


def test_the_seeded_ids_are_the_identity_the_sample_shipper_uses() -> None:
    """If these drift the sample logs silently stop belonging to a registered server."""
    assert str(seed.SAMPLE_LOG_SERVER_ID) == _compose_default("ALLOY_SERVER_ID")
    assert str(seed.SAMPLE_LOG_APPLICATION_ID) == _compose_default("ALLOY_APPLICATION_ID")


def test_the_inventory_hostname_differs_from_the_one_the_shipper_labels_with() -> None:
    """The smoke test relies on this to prove the API never serves the shipper's label."""
    assert seed.SAMPLE_LOG_HOSTNAME == "sample-log-host.demo.invalid"
    assert _compose_default_hostname() != seed.SAMPLE_LOG_HOSTNAME


def _compose_default_hostname() -> str:
    match = re.search(r"ALLOY_HOSTNAME: \$\{ALLOY_HOSTNAME:-([^}]+)\}", COMPOSE.read_text("utf-8"))
    assert match
    return match.group(1)
