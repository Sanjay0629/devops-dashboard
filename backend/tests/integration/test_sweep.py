"""The status sweep, against real rows (M2.5 commit 2).

What needs a real PostgreSQL:

* **Which rows the sweep may move.** Only ``MANAGED`` / ``STALE`` /
  ``UNREACHABLE`` on hosts Prometheus scrapes — never ``PENDING``, ``RETIRED``,
  a demo host or an unscraped one (D1).
* **Every move is audited** to a named system actor, in the transaction that
  made it (mandatory rule 7).
* **Migration 0006.** A sweep flip must not move ``updated_at`` or an open edit
  form fails ``409``; a person's change to ``status`` still must.
* **D2.** No reference time means no judgement at all.

Prometheus is not involved: the sweep reads ``servers`` and a reference time.
"""
# ruff: noqa: F811 — fixtures are imported by name and used as arguments

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.monitoring import marker, sweep
from tests.integration.test_auth_flow import redis_url  # noqa: F401
from tests.integration.test_monitoring import (  # noqa: F401 — isolated_fleet is used by name
    insert_server,
    isolated_fleet,
    row,
    run_probe,
    script,
    unique,
)
from tests.metrics.fake_prometheus import NOW, FakePrometheus

REFERENCE = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def seen_at(engine: Engine, server_id: str, seconds_ago: float | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE servers SET last_seen_at = :at WHERE id = :id"),
            {
                "id": server_id,
                "at": None if seconds_ago is None else REFERENCE - timedelta(seconds=seconds_ago),
            },
        )


def status_of(engine: Engine, server_id: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT status FROM servers WHERE id = :id"), {"id": server_id}
            ).scalar_one()
        )


def changes(engine: Engine, server_id: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(entry)
            for entry in connection.execute(
                text(
                    "SELECT actor_type, actor_id, actor_label, action_type, target_type,"
                    " environment, outcome, sanitized_metadata FROM audit_events"
                    " WHERE target_id = :id AND action_type = 'SERVER_STATUS_CHANGED'"
                    " ORDER BY occurred_at"
                ),
                {"id": server_id},
            )
            .mappings()
            .all()
        ]


def make(
    engine: Engine, *, status: str = "MANAGED", age: float | None, environment: str = "staging"
) -> str:
    server_id = insert_server(engine, instance=unique("sw"), status=status, environment=environment)
    seen_at(engine, server_id, age)
    return server_id


def run(engine: Engine, reference: datetime | None = REFERENCE) -> dict[str, int | str]:
    return sweep.sweep(engine, get_settings(), reference)


# --- the transitions ----------------


def test_each_age_lands_on_its_status(isolated_fleet: Engine) -> None:
    fresh = make(isolated_fleet, age=5)
    lagging = make(isolated_fleet, age=100)  # warning is still MANAGED: no such status
    stale = make(isolated_fleet, age=400)
    gone = make(isolated_fleet, age=1_000)

    result = run(isolated_fleet)

    assert result["outcome"] == "swept"
    assert [status_of(isolated_fleet, s) for s in (fresh, lagging, stale, gone)] == [
        "MANAGED",
        "MANAGED",
        "STALE",
        "UNREACHABLE",
    ]
    assert result["changed"] == 2


def test_a_host_that_comes_back_returns_to_managed(isolated_fleet: Engine) -> None:
    server_id = make(isolated_fleet, status="UNREACHABLE", age=3)

    run(isolated_fleet)

    assert status_of(isolated_fleet, server_id) == "MANAGED"
    (event,) = changes(isolated_fleet, server_id)
    assert event["sanitized_metadata"]["from"] == "UNREACHABLE"
    assert event["sanitized_metadata"]["to"] == "MANAGED"


def test_a_second_sweep_changes_nothing_and_audits_nothing(isolated_fleet: Engine) -> None:
    server_id = make(isolated_fleet, age=400)
    run(isolated_fleet)
    again = run(isolated_fleet)

    assert again["changed"] == 0
    assert len(changes(isolated_fleet, server_id)) == 1


def test_the_boundary_is_strict_end_to_end(isolated_fleet: Engine) -> None:
    on_the_line = make(isolated_fleet, age=300)
    over = make(isolated_fleet, age=301)

    run(isolated_fleet)

    assert status_of(isolated_fleet, on_the_line) == "MANAGED"
    assert status_of(isolated_fleet, over) == "STALE"


def test_never_seen_is_left_alone_not_called_stale(isolated_fleet: Engine) -> None:
    server_id = make(isolated_fleet, age=None)

    run(isolated_fleet)

    assert status_of(isolated_fleet, server_id) == "MANAGED"
    assert changes(isolated_fleet, server_id) == []


# --- what it may not touch ----------------


def test_it_never_moves_a_row_it_does_not_own(isolated_fleet: Engine) -> None:
    pending = make(isolated_fleet, status="PENDING", age=5_000)
    retired = make(isolated_fleet, status="RETIRED", age=5_000)

    run(isolated_fleet)

    assert status_of(isolated_fleet, pending) == "PENDING"
    assert status_of(isolated_fleet, retired) == "RETIRED"


def test_it_never_moves_an_unscraped_or_demo_server(isolated_fleet: Engine) -> None:
    off = insert_server(isolated_fleet, instance=unique("off"), status="MANAGED", monitoring=False)
    demo = insert_server(isolated_fleet, instance="web.demo.invalid:9100", status="MANAGED")
    no_instance = insert_server(isolated_fleet, instance=None, status="MANAGED")
    for server_id in (off, demo, no_instance):
        seen_at(isolated_fleet, server_id, 5_000)

    run(isolated_fleet)

    for server_id in (off, demo, no_instance):
        assert status_of(isolated_fleet, server_id) == "MANAGED"
        assert changes(isolated_fleet, server_id) == []


def test_a_soft_deleted_server_is_not_moved(isolated_fleet: Engine) -> None:
    server_id = insert_server(
        isolated_fleet, instance=unique("del"), status="MANAGED", deleted=True
    )
    seen_at(isolated_fleet, server_id, 5_000)

    run(isolated_fleet)

    assert status_of(isolated_fleet, server_id) == "MANAGED"


def test_a_change_made_mid_sweep_wins(
    isolated_fleet: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UPDATE is conditional on the status the sweep read (D1)."""
    server_id = make(isolated_fleet, age=400)
    real = sweep.classify_health

    def racing(*args: Any) -> Any:
        with isolated_fleet.begin() as connection:
            connection.execute(
                text("UPDATE servers SET status = 'RETIRED' WHERE id = :id"), {"id": server_id}
            )
        return real(*args)

    monkeypatch.setattr(sweep, "classify_health", racing)

    result = run(isolated_fleet)

    assert status_of(isolated_fleet, server_id) == "RETIRED"
    assert result["changed"] == 0
    assert changes(isolated_fleet, server_id) == []


# --- D2: no reference, no judgement ----------------


def test_with_no_reference_nothing_ages(isolated_fleet: Engine) -> None:
    """A Prometheus outage must not look like a fleet outage (issue 67)."""
    old = make(isolated_fleet, age=100_000)
    parked = make(isolated_fleet, status="UNREACHABLE", age=3)

    result = run(isolated_fleet, None)

    assert result["outcome"] == "no_reference"
    assert status_of(isolated_fleet, old) == "MANAGED"
    # Not even toward health: recovery needs the same proof Prometheus answered.
    assert status_of(isolated_fleet, parked) == "UNREACHABLE"
    assert changes(isolated_fleet, old) == []


def test_a_frozen_reference_freezes_the_fleet(isolated_fleet: Engine) -> None:
    """During an outage the marker stops, so re-sweeping later changes nothing."""
    server_id = make(isolated_fleet, age=10)
    run(isolated_fleet)
    # Wall-clock time passes; the reference does not. Nothing may age.
    for _ in range(3):
        run(isolated_fleet)
    assert status_of(isolated_fleet, server_id) == "MANAGED"


# --- audit (mandatory rule 7) ----------------


def test_a_move_is_audited_to_a_named_system_actor(isolated_fleet: Engine) -> None:
    server_id = make(isolated_fleet, age=400, environment="production")

    run(isolated_fleet)

    (event,) = changes(isolated_fleet, server_id)
    assert event["actor_type"] == "SYSTEM"
    assert event["actor_id"] is None
    assert event["actor_label"] == sweep.ACTOR_LABEL
    assert event["target_type"] == "server"
    assert event["environment"] == "production"
    assert event["outcome"] == "SUCCESS"
    metadata = event["sanitized_metadata"]
    assert (metadata["from"], metadata["to"], metadata["health_state"]) == (
        "MANAGED",
        "STALE",
        "stale",
    )
    assert metadata["age_seconds"] == 400
    # Evidence, not a credential or a free-text field a person typed.
    assert set(metadata) == {"from", "to", "health_state", "age_seconds", "reference_at"}


# --- per-environment thresholds -------------------------------------------------------------------


def test_thresholds_are_per_environment(
    isolated_fleet: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCP_HEALTH_THRESHOLDS", '{"production": {"stale_after_seconds": 120}}')
    get_settings.cache_clear()
    prod = make(isolated_fleet, age=200, environment="production")
    staging = make(isolated_fleet, age=200, environment="staging")

    run(isolated_fleet)

    assert status_of(isolated_fleet, prod) == "STALE"
    assert status_of(isolated_fleet, staging) == "MANAGED"


# --- migration 0006: updated_at ----------------


def test_a_sweep_flip_does_not_move_updated_at(isolated_fleet: Engine) -> None:
    """Otherwise an open edit form fails 409 because a host went quiet."""
    server_id = make(isolated_fleet, age=400)
    before = row(isolated_fleet, server_id)["updated_at"]

    run(isolated_fleet)

    assert status_of(isolated_fleet, server_id) == "STALE"
    assert row(isolated_fleet, server_id)["updated_at"] == before


@pytest.mark.parametrize(
    ("old", "new"),
    [("PENDING", "MANAGED"), ("MANAGED", "PENDING"), ("MANAGED", "RETIRED"), ("STALE", "RETIRED")],
)
def test_a_persons_status_change_still_moves_updated_at(
    isolated_fleet: Engine, old: str, new: str
) -> None:
    server_id = insert_server(isolated_fleet, instance=unique("tok"), status=old)
    before = row(isolated_fleet, server_id)["updated_at"]
    with isolated_fleet.begin() as connection:
        connection.execute(
            text("UPDATE servers SET status = :new WHERE id = :id"), {"id": server_id, "new": new}
        )
    assert row(isolated_fleet, server_id)["updated_at"] > before


def test_a_flip_together_with_an_edit_moves_updated_at(isolated_fleet: Engine) -> None:
    server_id = insert_server(isolated_fleet, instance=unique("mix"), status="MANAGED")
    before = row(isolated_fleet, server_id)["updated_at"]
    with isolated_fleet.begin() as connection:
        connection.execute(
            text("UPDATE servers SET status = 'STALE', owner_team = 'platform' WHERE id = :id"),
            {"id": server_id},
        )
    assert row(isolated_fleet, server_id)["updated_at"] > before


# --- the marker, written by the probe ----------------


def test_the_probe_records_that_prometheus_answered(
    isolated_fleet: Engine,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis

    monkeypatch.setenv("DCP_REDIS_URL", redis_url)
    get_settings.cache_clear()
    client = redis.Redis.from_url(redis_url)
    client.delete(marker.MARKER_KEY)
    instance = unique("mk")
    insert_server(isolated_fleet, instance=instance)
    fake = FakePrometheus()
    script(fake, [instance], {instance: "1"}, {instance: "1"}, seen=NOW - 2)

    before = datetime.now(UTC)
    run_probe(isolated_fleet, fake)

    answered = marker.read_marker(client)
    assert answered is not None
    assert before - timedelta(seconds=1) <= answered <= datetime.now(UTC) + timedelta(seconds=1)
    client.delete(marker.MARKER_KEY)


def test_a_probe_that_could_not_ask_prometheus_leaves_the_marker_alone(
    isolated_fleet: Engine,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis

    from app.workers.tasks import monitoring

    monkeypatch.setenv("DCP_REDIS_URL", redis_url)
    monkeypatch.delenv("DCP_PROMETHEUS_URL")
    get_settings.cache_clear()
    client = redis.Redis.from_url(redis_url)
    old = REFERENCE - timedelta(hours=1)
    marker.write_marker(client, old)
    insert_server(isolated_fleet, instance=unique("mk2"))

    monitoring.probe_agents()

    assert marker.read_marker(client) == old
    client.delete(marker.MARKER_KEY)


def test_the_task_reads_the_marker_and_sweeps(
    isolated_fleet: Engine,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis

    from app.workers.tasks import monitoring

    monkeypatch.setenv("DCP_REDIS_URL", redis_url)
    get_settings.cache_clear()
    client = redis.Redis.from_url(redis_url)
    server_id = make(isolated_fleet, age=400)

    client.delete(marker.MARKER_KEY)
    assert monitoring.sweep_status()["outcome"] == "no_reference"
    assert status_of(isolated_fleet, server_id) == "MANAGED"

    marker.write_marker(client, REFERENCE)
    assert monitoring.sweep_status()["outcome"] == "swept"
    assert status_of(isolated_fleet, server_id) == "STALE"
    client.delete(marker.MARKER_KEY)


def test_the_sweep_is_scheduled() -> None:
    from app.workers.celery_app import celery_app

    entry = next(
        e
        for e in celery_app.conf.beat_schedule.values()
        if e["task"] == "dcp.monitoring.sweep_status"
    )
    assert entry["schedule"] == 60.0
    assert entry["options"] == {"queue": "integrations"}
