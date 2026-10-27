"""Dashboard unit tests: honest data states, and the cache key's scope segment.

Unit rather than integration, deliberately. The two things checked here are
pure functions, and the security-relevant one —
"does the cache key depend on the caller's scope?" — is worth catching in the
fast suite, before anything has to start a database. The end-to-end proof that
two callers really do get different answers is in
``tests/integration/test_dashboard_api.py``.
"""

from __future__ import annotations

import uuid

from app.authz.matcher import Grant
from app.dashboard.schemas import (
    ActiveAlertsSection,
    RecentDeploymentsSection,
    ServerHealthRollup,
    UtilisationSection,
)
from app.dashboard.service import DashboardFilterSet, cache_key

FULL_SCOPE: dict[str, list[str]] = {
    "environment": ["development", "staging", "production", "other"],
    "cloud_account": ["*"],
    "server": ["*"],
    "application": ["*"],
}
STAGING_SCOPE: dict[str, list[str]] = {"environment": ["staging"], "server": ["*"]}


def read_grant(scope: dict[str, list[str]], effect: str = "ALLOW") -> Grant:
    return Grant(resource_type="server", action="read", effect=effect, scope=scope)


# --- the cache key carries scope (M1.6 security check) ------------------------


def test_two_scopes_produce_different_cache_keys() -> None:
    """The whole point: one operator's aggregate must not be served to another.

    Identical endpoint, identical filters — only the caller's readable set
    differs. If the key ignored scope these would collide and the second
    caller would be handed the first caller's counts without a query ever
    running.
    """
    filters = DashboardFilterSet().cache_part()
    full = cache_key("summary", [read_grant(FULL_SCOPE)], filters)
    staging = cache_key("summary", [read_grant(STAGING_SCOPE)], filters)
    assert full != staging


def test_an_added_deny_changes_the_cache_key() -> None:
    """A deny changes the readable set as much as an allow does.

    Digesting only the allows would let a caller who has been denied an
    environment read the entry written before the denial existed.
    """
    filters = DashboardFilterSet().cache_part()
    allow_only = cache_key("summary", [read_grant(FULL_SCOPE)], filters)
    with_deny = cache_key(
        "summary",
        [read_grant(FULL_SCOPE), read_grant({"environment": ["production"]}, effect="DENY")],
        filters,
    )
    assert allow_only != with_deny


def test_the_operation_read_scope_is_part_of_the_cache_key() -> None:
    """``recent_operations`` is scoped by ``operation:read`` (M4.6), so two callers who read the
    same servers but different operations must not share an entry."""
    filters = DashboardFilterSet().cache_part()
    servers = read_grant(FULL_SCOPE)
    wide = Grant("operation", "read", "ALLOW", FULL_SCOPE)
    narrow = Grant("operation", "read", "ALLOW", STAGING_SCOPE)
    assert cache_key("summary", [servers, wide], filters) != cache_key(
        "summary", [servers, narrow], filters
    )
    assert cache_key("summary", [servers], filters) != cache_key(
        "summary", [servers, wide], filters
    )


def test_a_grant_that_is_not_a_scope_input_does_not_split_the_cache() -> None:
    filters = DashboardFilterSet().cache_part()
    servers = read_grant(FULL_SCOPE)
    unrelated = Grant("alert", "read", "ALLOW", STAGING_SCOPE)
    assert cache_key("summary", [servers], filters) == cache_key(
        "summary", [servers, unrelated], filters
    )


def test_the_deployment_read_scope_is_part_of_the_cache_key() -> None:
    """``recent_deployments`` is scoped by ``deployment:read`` (M5.2): two callers who read the
    same servers but different deployments must not share an entry."""
    filters = DashboardFilterSet().cache_part()
    servers = read_grant(FULL_SCOPE)
    wide = Grant("deployment", "read", "ALLOW", FULL_SCOPE)
    narrow = Grant("deployment", "read", "ALLOW", STAGING_SCOPE)
    assert cache_key("summary", [servers, wide], filters) != cache_key(
        "summary", [servers, narrow], filters
    )
    assert cache_key("summary", [servers], filters) != cache_key(
        "summary", [servers, wide], filters
    )


def test_equivalent_scopes_share_a_cache_key() -> None:
    """Normalised, so ordering is not a cache miss.

    The other half of the rule: a key that changed on grant *ordering* would
    be correct and useless, and a cache nobody hits gets deleted.
    """
    filters = DashboardFilterSet().cache_part()
    one = cache_key("summary", [read_grant({"environment": ["staging", "development"]})], filters)
    two = cache_key("summary", [read_grant({"environment": ["development", "staging"]})], filters)
    assert one == two


def test_grants_for_other_resources_do_not_change_the_key() -> None:
    """Only ``server:read`` feeds ``readable_servers``, so only it may feed the key."""
    filters = DashboardFilterSet().cache_part()
    base = cache_key("summary", [read_grant(FULL_SCOPE)], filters)
    with_extra = cache_key(
        "summary",
        [
            read_grant(FULL_SCOPE),
            Grant(resource_type="audit", action="read", effect="ALLOW", scope={}),
        ],
        filters,
    )
    assert base == with_extra


def test_different_filters_produce_different_cache_keys() -> None:
    grants = [read_grant(FULL_SCOPE)]
    unfiltered = cache_key("summary", grants, DashboardFilterSet().cache_part())
    production = cache_key(
        "summary", grants, DashboardFilterSet(environment=("production",)).cache_part()
    )
    assert unfiltered != production


def test_the_two_endpoints_do_not_share_a_cache_entry() -> None:
    grants = [read_grant(FULL_SCOPE)]
    filters = DashboardFilterSet().cache_part()
    assert cache_key("summary", grants, filters) != cache_key("health", grants, filters)


def test_the_cache_key_does_not_contain_the_scope_in_clear() -> None:
    """A key is printed in logs and listed by ``redis-cli --scan``.

    A grant naming specific server ids is an inventory listing, so the scope
    is digested rather than concatenated.
    """
    server = uuid.uuid4()
    key = cache_key(
        "summary", [read_grant({"server": [str(server)]})], DashboardFilterSet().cache_part()
    )
    assert str(server) not in key


def test_filters_are_echoed_as_applied_not_as_sent() -> None:
    applied = DashboardFilterSet(environment=("staging",), owner_team=("platform",)).echoed()
    assert applied.environment == ["staging"]
    assert applied.owner_team == ["platform"]
    # An absent filter is absent, not an empty list that reads as "filtered
    # to nothing".
    assert applied.provider is None
    assert applied.region is None


# --- honest data states (PRD §4.2 acceptance criterion) -----------------------


def test_a_section_with_no_source_defaults_to_null_never_zero() -> None:
    """The acceptance criterion, asserted on the models themselves.

    Constructed exactly as the service constructs them — with no values —
    because a default of ``0`` anywhere in these classes would render as "no
    critical servers" or "no CPU load" rather than "we do not know".
    """
    health = ServerHealthRollup(data_state="not_configured")
    assert (health.healthy, health.warning, health.critical, health.unknown) == (
        None,
        None,
        None,
        None,
    )

    utilisation = UtilisationSection(data_state="not_configured")
    assert utilisation.cpu_percent_avg is None
    assert utilisation.memory_percent_avg is None
    assert utilisation.disk_percent_max is None
    assert utilisation.network_bytes_per_second is None

    alerts = ActiveAlertsSection(data_state="not_configured")
    assert (alerts.critical, alerts.warning, alerts.info) == (None, None, None)


def test_a_recent_list_that_cannot_be_answered_is_null_not_an_empty_list() -> None:
    """``[]`` would claim "nothing happened recently". A section that cannot be asked says so."""
    section = RecentDeploymentsSection(data_state="unavailable")
    assert section.items is None


def test_a_section_without_a_source_carries_no_collected_at() -> None:
    """§4 rule 3 in the negative: nothing collected it, so there is no time."""
    section = UtilisationSection(data_state="not_configured")
    assert section.collected_at is None
