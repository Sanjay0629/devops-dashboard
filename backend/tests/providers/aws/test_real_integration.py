"""The ONLY check that touches AWS. Manual, non-production, never in CI (docs/TESTING.md §2.1).

    uv run pytest -m real_integration --integration-profile=nonprod-sandbox

Reads its target from the environment (not ``DCP_``-prefixed: these are test inputs, not
settings): ``REAL_AWS_ROLE_ARN``, ``REAL_AWS_REGION``, ``REAL_AWS_INSTANCE_ID``,
``REAL_AWS_UNIT`` (a harmless unit on that instance), ``DCP_AWS_SSM_DOCUMENT_VERSION``. The
runtime identity is the ambient role; no key may be set. The restart test also needs
``REAL_AWS_ALLOW_RESTART=yes``. Record the result in PROGRESS.md as a dated MANUAL CHECK;
until someone has, M4.3 has never run against AWS.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from app.providers.aws import build_adapter
from app.providers.types import ServerRef, ServiceAction, ServiceRef

pytestmark = pytest.mark.real_integration

_NEEDED = ("REAL_AWS_ROLE_ARN", "REAL_AWS_REGION", "REAL_AWS_INSTANCE_ID", "REAL_AWS_UNIT")


@pytest.fixture
def target() -> tuple[ServerRef, ServiceRef]:
    missing = [name for name in _NEEDED if not os.environ.get(name)]
    if missing:
        pytest.skip(f"missing {', '.join(missing)}")
    role = os.environ["REAL_AWS_ROLE_ARN"]
    ref = ServerRef(
        os.environ["REAL_AWS_INSTANCE_ID"],
        role.split(":")[4],
        "AWS_SSM",
        os.environ["REAL_AWS_REGION"],
        role,
    )
    return ref, ServiceRef(uuid.uuid4(), os.environ["REAL_AWS_UNIT"])


def test_the_instance_is_reachable_and_its_unit_has_a_state(
    target: tuple[ServerRef, ServiceRef],
) -> None:
    ref, service = target
    adapter = build_adapter()
    assert adapter.check_connectivity(ref).value.reachable is True  # type: ignore[union-attr]
    snapshot = adapter.list_managed_services(ref, service).value
    assert snapshot and snapshot[0].unit_name == service.unit_name


def test_a_restart_completes_and_is_verified(target: tuple[ServerRef, ServiceRef]) -> None:
    if os.environ.get("REAL_AWS_ALLOW_RESTART") != "yes":
        pytest.skip("set REAL_AWS_ALLOW_RESTART=yes to restart the unit on the sandbox instance")
    ref, service = target
    adapter = build_adapter()
    handle = adapter.execute_operation(
        ref, service, ServiceAction.RESTART, operation_id=uuid.uuid4()
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = adapter.get_operation_status(ref, handle).value
        assert status is not None
        if status.state != "IN_PROGRESS":
            break
        time.sleep(3)
    assert status.state == "COMPLETED"
    after = adapter.list_managed_services(ref, service).value
    assert after and after[0].state == "ACTIVE"
