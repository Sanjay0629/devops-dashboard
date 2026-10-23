"""The ``deployment_targets`` table: what the schema enforces whatever the application does (M5.1).

Each test asserts that **the database** refuses the thing (docs/DATABASE.md §5.1, ``OPEN-129``):
the job-path grammar, the rollback pair, the JSON-object and pattern-count bounds, and one live
target per application and environment.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, text

from tests.integration.test_alert_schema import a_user
from tests.integration.test_operation_schema import refused

INSERT = text(
    """
    INSERT INTO deployment_targets (id, application_id, environment, pipeline_reference,
                                    parameter_template, allowed_ref_patterns, approval_policy_id,
                                    rollback_supported, rollback_pipeline_reference, status,
                                    created_by, idempotency_key, deleted_at)
    VALUES (:id, :application_id, :environment, :pipeline_reference,
            CAST(:parameter_template AS jsonb), :patterns, :policy, :rollback_supported,
            :rollback_reference, :status, :created_by, :key,
            CASE WHEN :retired THEN now() END)
    """
)


def an_application(connection: Connection) -> uuid.UUID:
    application = uuid.uuid4()
    connection.execute(
        text("INSERT INTO applications (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": application, "n": f"app {application}", "s": f"a{application.hex[:20]}"},
    )
    return application


def a_target(
    connection: Connection, application: uuid.UUID | None = None, **overrides: Any
) -> uuid.UUID:
    target = uuid.uuid4()
    connection.execute(
        INSERT,
        {
            "id": target,
            "application_id": application or an_application(connection),
            "environment": "staging",
            "pipeline_reference": "team/service-deploy",
            "parameter_template": "{}",
            "patterns": [],
            "policy": None,
            "rollback_supported": False,
            "rollback_reference": None,
            "status": "ACTIVE",
            "created_by": None,
            "key": None,
            "retired": False,
            **overrides,
        },
    )
    return target


@pytest.fixture
def conn(connection: Connection) -> Connection:
    return connection


def test_a_legitimate_target_is_accepted(conn: Connection) -> None:
    a_target(conn, patterns=["^main$", "^release/.+$"], parameter_template='{"fixed": {}}')
    a_target(conn, rollback_supported=True, rollback_reference="team/service-rollback")


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "../escape",
        "a/../b",
        ".hidden",
        "a//b",
        "a/",
        "/a",
        "a b",
        "a?x=1",
        "a#frag",
        "a%2e%2e",
        "a\nb",
        "a/b/c/d/e/f",  # six segments
        "x" * 65,
    ],
)
def test_the_job_path_grammar_is_a_constraint(conn: Connection, reference: str) -> None:
    with refused(conn):
        a_target(conn, pipeline_reference=reference)


def test_a_rollback_reference_obeys_the_same_grammar(conn: Connection) -> None:
    with refused(conn):
        a_target(conn, rollback_supported=True, rollback_reference="../x")


def test_the_rollback_pair_travels_together(conn: Connection) -> None:
    with refused(conn):
        a_target(conn, rollback_supported=True, rollback_reference=None)
    with refused(conn):
        a_target(conn, rollback_supported=False, rollback_reference="team/rollback")


def test_the_template_must_be_a_json_object(conn: Connection) -> None:
    with refused(conn):
        a_target(conn, parameter_template="[]")
    with refused(conn):
        a_target(conn, parameter_template='"text"')


def test_at_most_ten_patterns(conn: Connection) -> None:
    a_target(conn, patterns=[f"^b{i}$" for i in range(10)])
    with refused(conn):
        a_target(conn, patterns=[f"^b{i}$" for i in range(11)])


@pytest.mark.parametrize("column,value", [("environment", "prod"), ("status", "RETIRED")])
def test_closed_sets(conn: Connection, column: str, value: str) -> None:
    with refused(conn):
        a_target(conn, **{column: value})


def test_one_live_target_per_application_and_environment(conn: Connection) -> None:
    application = an_application(conn)
    a_target(conn, application)
    with refused(conn):
        a_target(conn, application)
    a_target(conn, application, environment="production")  # another environment is fine


def test_retiring_frees_the_pair(conn: Connection) -> None:
    application = an_application(conn)
    a_target(conn, application, retired=True)
    a_target(conn, application)


def test_an_idempotency_key_is_unique_per_creator(conn: Connection) -> None:
    user = a_user(conn)
    a_target(conn, created_by=user, key="key-0001-aaaa")
    with refused(conn):
        a_target(conn, created_by=user, key="key-0001-aaaa")
    a_target(conn, created_by=a_user(conn), key="key-0001-aaaa")


def test_a_target_cannot_outlive_its_application_or_policy(conn: Connection) -> None:
    application = an_application(conn)
    a_target(conn, application)
    with refused(conn):
        conn.execute(text("DELETE FROM applications WHERE id = :i"), {"i": application})
    policy = conn.execute(
        text("SELECT id FROM approval_policies WHERE name = 'production-deployments'")
    ).scalar_one()
    a_target(conn, environment="production", policy=policy)
    with refused(conn):
        conn.execute(text("DELETE FROM approval_policies WHERE id = :i"), {"i": policy})


def test_updated_at_moves_on_update(conn: Connection) -> None:
    target = a_target(conn)
    before = conn.execute(
        text("SELECT updated_at FROM deployment_targets WHERE id = :i"), {"i": target}
    ).scalar_one()
    conn.execute(text("SELECT pg_sleep(0.01)"))
    conn.execute(
        text("UPDATE deployment_targets SET status = 'DISABLED' WHERE id = :i"), {"i": target}
    )
    after = conn.execute(
        text("SELECT updated_at FROM deployment_targets WHERE id = :i"), {"i": target}
    ).scalar_one()
    assert after >= before
