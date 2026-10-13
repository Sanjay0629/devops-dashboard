"""Audit immutability (AD-11, mandatory rule 10, PRD §4.12).

The acceptance criterion is "users cannot alter audit records through ordinary
application APIs", and the design goes further: the *database* refuses.

These tests matter because the first implementation here did not work. Revoking
UPDATE and DELETE showed the privileges as revoked in ``information_schema``
while both statements still succeeded, because the development role is a
superuser and superusers bypass privilege checks. The triggers are what closed
it, and this file is what stops that regressing silently.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.security

INSERT_EVENT = text(
    """
    INSERT INTO audit_events (id, occurred_at, actor_type, actor_label,
                              action_type, correlation_id, outcome,
                              sanitized_metadata)
    VALUES (:id, now(), 'SYSTEM', 'system:test', 'TEST_EVENT', :correlation_id,
            'SUCCESS', '{}'::jsonb)
    """
)


def _insert(connection: Connection) -> uuid.UUID:
    event_id = uuid.uuid4()
    connection.execute(INSERT_EVENT, {"id": event_id, "correlation_id": uuid.uuid4()})
    return event_id


def test_insert_is_permitted(connection: Connection) -> None:
    event_id = _insert(connection)
    found = connection.execute(
        text("SELECT count(*) FROM audit_events WHERE id = :id"), {"id": event_id}
    ).scalar()
    assert found == 1


def test_the_test_role_really_is_privileged(connection: Connection) -> None:
    """Guards against a false pass.

    If the test database user were an unprivileged role, the tests below would
    pass for the wrong reason and tell us nothing about the trigger. This
    asserts the hard case is the one being exercised.
    """
    is_superuser = connection.execute(
        text("SELECT usesuper FROM pg_user WHERE usename = current_user")
    ).scalar()
    is_owner = connection.execute(
        text(
            """
            SELECT pg_get_userbyid(relowner) = current_user
              FROM pg_class WHERE relname = 'audit_events'
            """
        )
    ).scalar()
    assert is_superuser or is_owner, (
        "the audit immutability tests are only meaningful when run as a role "
        "that grants alone would not stop"
    )


def test_update_is_refused(connection: Connection) -> None:
    _insert(connection)
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE audit_events SET outcome = 'FAILURE'"))


def test_delete_is_refused(connection: Connection) -> None:
    _insert(connection)
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("DELETE FROM audit_events"))


def test_truncate_is_refused(connection: Connection) -> None:
    """Row triggers never see TRUNCATE, hence its own statement-level trigger."""
    _insert(connection)
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("TRUNCATE audit_events"))


def test_targeted_update_by_primary_key_is_refused(connection: Connection) -> None:
    """The realistic shape of an attempt to doctor one specific record."""
    event_id = _insert(connection)
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text("UPDATE audit_events SET actor_label = 'someone else' WHERE id = :id"),
            {"id": event_id},
        )


def test_update_through_a_partition_is_also_refused(connection: Connection) -> None:
    """Writing to the partition directly must not bypass the parent's trigger."""
    _insert(connection)
    partition = connection.execute(
        text(
            """
            SELECT c.relname FROM pg_class c
              JOIN pg_inherits i ON i.inhrelid = c.oid
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE p.relname = 'audit_events'
             ORDER BY c.relname
             LIMIT 1
            """
        )
    ).scalar()
    assert partition is not None
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"UPDATE {partition} SET outcome = 'FAILURE'"))  # noqa: S608


def test_grants_are_also_revoked(connection: Connection) -> None:
    """Layer 1 of the defence: correct once the app uses a non-superuser role."""
    privileges = {
        row[0]
        for row in connection.execute(
            text(
                """
                SELECT privilege_type FROM information_schema.table_privileges
                 WHERE table_name = 'audit_events' AND grantee = current_user
                """
            )
        )
    }
    assert "SELECT" in privileges
    assert "INSERT" in privileges
    assert "UPDATE" not in privileges
    assert "DELETE" not in privileges


def test_actor_attribution_is_enforced(connection: Connection) -> None:
    """Mandatory rule 10: a USER event must name its user."""
    with pytest.raises(DBAPIError):
        connection.execute(
            text(
                """
                INSERT INTO audit_events (id, occurred_at, actor_type, actor_id,
                                          actor_label, action_type, correlation_id,
                                          outcome)
                VALUES (:id, now(), 'USER', NULL, 'nobody', 'TEST', :cid, 'SUCCESS')
                """
            ),
            {"id": uuid.uuid4(), "cid": uuid.uuid4()},
        )


def test_system_actor_must_not_carry_a_user_id(connection: Connection) -> None:
    """The CHECK is an equivalence, so the reverse is a violation too."""
    connection.execute(
        text(
            """
            INSERT INTO users (id, identity_provider_subject,
                               identity_provider_issuer, email, display_name,
                               status, created_at, updated_at)
            VALUES (:uid, 'subject', 'https://idp.example.invalid',
                    'placeholder@example.invalid', 'Placeholder', 'ACTIVE',
                    now(), now())
            """
        ),
        {"uid": (user_id := uuid.uuid4())},
    )
    with pytest.raises(DBAPIError):
        connection.execute(
            text(
                """
                INSERT INTO audit_events (id, occurred_at, actor_type, actor_id,
                                          actor_label, action_type, correlation_id,
                                          outcome)
                VALUES (:id, now(), 'SYSTEM', :uid, 'system:test', 'TEST', :cid,
                        'SUCCESS')
                """
            ),
            {"id": uuid.uuid4(), "uid": user_id, "cid": uuid.uuid4()},
        )
