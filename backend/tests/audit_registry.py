"""Every state-changing route, and what it must leave in the audit trail (M4.6, ``OPEN-114``).

PRD §4.12: "every privileged operation can be traced to its initiating identity." The way that
stays true as endpoints are added is a registry that is checked against the **live routing table**:
a new ``POST`` / ``PATCH`` / ``PUT`` / ``DELETE`` with no entry fails the build, so adding one
forces somebody to say what it audits. This module is only data; the checks are
``tests/authz/test_audit_registry.py`` (the registry against the routing table and the source) and
``tests/integration/test_audit_completeness.py`` (each *driven* route, run and read back).

Four kinds, and only these:

``driven``
    the integration test calls the route and asserts, from the database, that the request's own
    correlation id carries each listed action, written by the caller.
``elsewhere``
    the route needs machinery the completeness test does not build (a stubbed AWS layer, a seeded
    alert). Another named test asserts the action; the static check confirms that test exists and
    mentions the action, so the claim cannot rot. Counted and reported, never silent.
``read_only``
    a ``POST`` that changes nothing (a query whose body is too large for a URL).
``own``
    written outside a request's own session by design, with the test that covers it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    kind: str
    actions: tuple[str, ...] = ()
    #: ``elsewhere`` / ``own``: ``"<file>::<test>"`` under ``tests/integration/``.
    test: str | None = None
    why: str = ""


def driven(*actions: str) -> Entry:
    return Entry("driven", actions)


def elsewhere(test: str, *actions: str, why: str) -> Entry:
    return Entry("elsewhere", actions, test, why)


READ_ONLY = "a query whose body is too large for a URL; it changes nothing"
NEEDS_AWS = (
    "needs the stubbed AWS layer (tests/providers/aws/harness.py), which this test does not build"
)
NEEDS_ALERT = "needs a seeded alert row and its delivery, built by that file's own rig"

REGISTRY: dict[tuple[str, str], Entry] = {
    # --- administration -----------------------------------------------------------------------
    ("POST", "/api/v1/admin/users"): driven("USER_CREATED"),
    ("PATCH", "/api/v1/admin/users/{user_id}"): driven("USER_DISABLED"),
    ("POST", "/api/v1/admin/users/{user_id}/roles"): driven("ROLE_GRANTED"),
    ("DELETE", "/api/v1/admin/users/{user_id}/roles/{user_role_id}"): driven("ROLE_REVOKED"),
    ("POST", "/api/v1/admin/users/{user_id}/sessions/terminate"): driven("SESSION_TERMINATED"),
    ("POST", "/api/v1/admin/settings/read-only-mode"): driven("READ_ONLY_MODE_CHANGED"),
    ("POST", "/api/v1/admin/approval-policies"): driven("APPROVAL_POLICY_CHANGED"),
    ("PATCH", "/api/v1/admin/approval-policies/{policy_id}"): driven("APPROVAL_POLICY_CHANGED"),
    ("DELETE", "/api/v1/admin/approval-policies/{policy_id}"): driven("APPROVAL_POLICY_CHANGED"),
    # --- inventory ----------------------------------------------------------------------------
    ("POST", "/api/v1/servers"): driven("SERVER_REGISTERED"),
    ("PATCH", "/api/v1/servers/{server_id}"): driven("SERVER_UPDATED"),
    ("DELETE", "/api/v1/servers/{server_id}"): driven("SERVER_RETIRED"),
    ("POST", "/api/v1/servers/{server_id}/applications"): driven("SERVER_UPDATED"),
    ("DELETE", "/api/v1/servers/{server_id}/applications/{application_id}"): driven(
        "SERVER_UPDATED"
    ),
    ("POST", "/api/v1/servers/{server_id}/services"): driven("SERVICE_REGISTERED"),
    ("PATCH", "/api/v1/servers/{server_id}/services/{service_name}"): driven("SERVICE_UPDATED"),
    ("DELETE", "/api/v1/servers/{server_id}/services/{service_name}"): driven(
        "SERVICE_DEREGISTERED"
    ),
    ("POST", "/api/v1/applications"): driven("APPLICATION_REGISTERED"),
    ("PATCH", "/api/v1/applications/{application_id}"): driven("APPLICATION_UPDATED"),
    ("DELETE", "/api/v1/applications/{application_id}"): driven("APPLICATION_ARCHIVED"),
    ("POST", "/api/v1/cloud-accounts"): elsewhere(
        "test_cloud_accounts_admin.py::test_an_aws_account_is_registered_with_a_role_arn_and_shows_no_secret",
        "CLOUD_ACCOUNT_REGISTERED",
        why=NEEDS_AWS,
    ),
    ("PATCH", "/api/v1/cloud-accounts/{account_id}"): elsewhere(
        "test_cloud_accounts_admin.py::test_an_edit_changes_what_it_names_and_is_audited_by_field_name_only",
        "CLOUD_ACCOUNT_UPDATED",
        why=NEEDS_AWS,
    ),
    ("POST", "/api/v1/cloud-accounts/{account_id}/verify"): elsewhere(
        "test_cloud_accounts_admin.py::test_verify_reports_what_a_read_only_probe_can_prove_and_lists_what_it_cannot",
        "CLOUD_ACCOUNT_VERIFIED",
        why=NEEDS_AWS,
    ),
    ("POST", "/api/v1/cloud-accounts/{account_id}/sync"): elsewhere(
        "test_cloud_accounts_admin.py::test_a_sync_request_enqueues_two_ids_and_nothing_else",
        "CLOUD_ACCOUNT_SYNC_REQUESTED",
        why=NEEDS_AWS,
    ),
    ("POST", "/api/v1/servers/{server_id}/connectivity-check"): elsewhere(
        "test_cloud_accounts_admin.py::test_a_reachable_server_is_reported_and_nothing_about_it_changes",
        "SERVER_CONNECTIVITY_CHECKED",
        why=NEEDS_AWS,
    ),
    # --- operations and approvals -------------------------------------------------------------
    ("POST", "/api/v1/servers/{server_id}/services/{service_name}/operations"): driven(
        "SERVICE_OPERATION_REQUESTED"
    ),
    ("POST", "/api/v1/operations/{operation_id}/cancel"): driven("SERVICE_OPERATION_CANCELLED"),
    ("POST", "/api/v1/operations/{operation_id}/approve"): driven("SERVICE_OPERATION_APPROVED"),
    ("POST", "/api/v1/operations/{operation_id}/reject"): driven("SERVICE_OPERATION_REJECTED"),
    # --- alerts and notifications -------------------------------------------------------------
    ("POST", "/api/v1/alerts/{alert_id}/acknowledge"): elsewhere(
        "test_alerts_api.py::test_acknowledging_marks_it_acknowledged_and_never_resolved",
        "ALERT_ACKNOWLEDGED",
        why=NEEDS_ALERT,
    ),
    ("POST", "/api/v1/alerts/{alert_id}/assign"): elsewhere(
        "test_alerts_api.py::test_assigning_to_yourself_to_a_colleague_and_unassigning",
        "ALERT_ASSIGNED",
        why=NEEDS_ALERT,
    ),
    ("POST", "/api/v1/alerts/{alert_id}/notifications/{delivery_id}/retry"): elsewhere(
        "test_notifications_api.py::test_retry_makes_the_delivery_due_again_with_a_fresh_budget",
        "NOTIFICATION_RETRY_REQUESTED",
        why=NEEDS_ALERT,
    ),
    ("POST", "/api/v1/notification-channels"): driven("NOTIFICATION_CHANNEL_CHANGED"),
    ("PATCH", "/api/v1/notification-channels/{channel_id}"): driven("NOTIFICATION_CHANNEL_CHANGED"),
    # --- the audit trail itself ---------------------------------------------------------------
    ("POST", "/api/v1/audit-events/export"): driven("AUDIT_EXPORT_REQUESTED"),
    # --- reads that happen to be POSTs --------------------------------------------------------
    ("POST", "/api/v1/logs/query"): Entry("read_only", why=READ_ONLY),
    ("POST", "/api/v1/metrics/compare"): Entry("read_only", why=READ_ONLY),
    # --- written by their own code ------------------------------------------------------------
    ("POST", "/api/v1/auth/logout"): driven("LOGOUT"),
    ("POST", "/api/v1/webhooks/alerts"): Entry(
        "own",
        ("ALERT_INGESTED",),
        "test_alert_webhook.py::test_a_crafted_alert_cannot_cause_anything_but_an_alert_row_as_text",
        "public, authenticated by a shared secret; its actor is the named system identity",
    ),
}
