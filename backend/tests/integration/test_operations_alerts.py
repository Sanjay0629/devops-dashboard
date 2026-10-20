"""An operation that fails is an alert; the next success closes it (M4.3, ``OPEN-89``)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.alerts.catalogue import OPERATION_RULE
from app.operations import alerting
from app.providers.errors import Timeout
from tests.integration.inventory_helpers import revoke_all
from tests.integration.test_operations_worker import (  # noqa: F401 -- fixture used by name
    World,
    no_sleep,
    world,
)
from tests.operations.fakes import Scripted


def alerts(world: World, target_id: uuid.UUID) -> list[dict[str, Any]]:  # noqa: F811
    with world.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT * FROM alert_events WHERE source = 'DCP_OPERATION' AND target_id = :t"
                " ORDER BY first_seen_at"
            ),
            {"t": target_id},
        ).mappings()
        return [dict(r) for r in rows]


def test_a_failed_operation_raises_one_alert_about_its_service(world: World) -> None:  # noqa: F811
    target = world.add()
    world.adapter = Scripted(statuses=("FAILED",), provider_reason="command_failed")
    operation = world.operation(target)

    assert world.run(operation) == "FAILED:PROVIDER_REPORTED_FAILURE"

    [alert] = alerts(world, target.service)
    assert (alert["rule_name"], alert["severity"], alert["status"]) == (
        OPERATION_RULE.name,
        "WARNING",
        "FIRING",
    )
    assert alert["target_type"] == "service" and alert["environment"] == "staging"
    assert alert["summary"] == "A restart operation ended FAILED: PROVIDER_REPORTED_FAILURE"
    assert alert["details"]["operation_id"] == str(operation)


def test_repeated_failures_of_one_target_are_one_alert_counted(world: World) -> None:  # noqa: F811
    target = world.add()
    world.adapter = Scripted(statuses=("FAILED",))
    for _ in range(3):
        world.run(world.operation(target))
    [alert] = alerts(world, target.service)
    assert alert["occurrence_count"] == 3


def test_the_next_success_on_the_target_resolves_it(world: World) -> None:  # noqa: F811
    target = world.add()
    world.adapter = Scripted(statuses=("FAILED",))
    world.run(world.operation(target))
    world.adapter = Scripted()
    assert world.run(world.operation(target)) == "SUCCEEDED"
    [alert] = alerts(world, target.service)
    assert alert["status"] == "RESOLVED" and alert["resolved_at"] is not None
    # And a failure after that is a new alert, not a reopened one.
    world.adapter = Scripted(statuses=("FAILED",))
    world.run(world.operation(target))
    assert [a["status"] for a in alerts(world, target.service)] == ["RESOLVED", "FIRING"]


def test_a_success_elsewhere_does_not_resolve_this_targets_alert(world: World) -> None:  # noqa: F811
    broken, fine = world.add(), world.add()
    world.adapter = Scripted(statuses=("FAILED",))
    world.run(world.operation(broken))
    world.adapter = Scripted()
    world.run(world.operation(fine))
    assert alerts(world, broken.service)[0]["status"] == "FIRING"


@pytest.mark.parametrize("reason", ["TIMED_OUT", "VERIFICATION_MISMATCH", "UNAVAILABLE"])
def test_each_way_an_operation_can_go_wrong_alerts(world: World, reason: str) -> None:  # noqa: F811
    target = world.add()
    if reason == "TIMED_OUT":
        world.adapter = Scripted(status_error=Timeout(adapter_id="scripted"))
    elif reason == "VERIFICATION_MISMATCH":
        world.adapter = Scripted(after=("INACTIVE",))
    else:
        world.adapter = Scripted(list_error=Timeout(adapter_id="scripted"))
    world.run(world.operation(target))
    assert len(alerts(world, target.service)) == 1


def test_a_target_that_failed_revalidation_alerts_because_inventory_is_stale(
    world: World,  # noqa: F811
) -> None:
    target = world.add()
    world.adapter = Scripted(info_resource="i-someoneelse")
    assert world.run(world.operation(target)) == "FAILED:TARGET_VALIDATION_FAILED"
    assert len(alerts(world, target.service)) == 1


def test_an_operation_that_expired_before_anyone_claimed_it_alerts(world: World) -> None:  # noqa: F811
    target = world.add()
    operation = world.operation(target, deadline_in=-60.0)
    assert world.run(operation) == "TIMED_OUT:EXPIRED_BEFORE_START"
    [alert] = alerts(world, target.service)
    assert "EXPIRED_BEFORE_START" in alert["summary"]


def test_a_denial_is_the_controls_working_and_raises_nothing(world: World) -> None:  # noqa: F811
    target = world.add()
    revoke_all(world.engine, world.user)
    assert world.run(world.operation(target)) == "FAILED:PERMISSION_REVOKED"
    assert alerts(world, target.service) == []


def test_a_failure_to_write_the_alert_never_undoes_the_operations_end(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("alert store is down")

    monkeypatch.setattr(alerting.store, "fire", broken)
    target = world.add()
    world.adapter = Scripted(statuses=("FAILED",))
    operation = world.operation(target)
    assert world.run(operation) == "FAILED:PROVIDER_REPORTED_FAILURE"
    row = world.row(operation)
    assert row["execution_status"] == "FAILED"
    assert world.audit(operation, "SERVICE_OPERATION_COMPLETED")  # the audit row committed too
    assert alerts(world, target.service) == []


def test_the_alert_text_is_a_fixed_template_over_closed_values(world: World) -> None:  # noqa: F811
    target = world.add()
    world.adapter = Scripted(statuses=("FAILED",), provider_reason="command_failed")
    world.run(world.operation(target))
    [alert] = alerts(world, target.service)
    assert set(alert["details"]) == {"operation_id", "action", "status", "failure_reason"}
