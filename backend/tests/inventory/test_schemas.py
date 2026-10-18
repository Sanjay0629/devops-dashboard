"""Request-schema validation for inventory (IMPLEMENTATION_PLAN.md M1.5 unit tests).

Covers the four things the plan names — hostname, IP, enums, immutable fields —
plus the two validators that reach outside the model: ``metrics_instance``'s
format and the SSRF allowlist on registered URLs.

The immutable-field cases matter most. §14.4 requires ``422 not_permitted`` for
an attempt to change ``cloud_account_id`` or ``provider_resource_id``, and the
mechanism here is *absence* — those fields are not on the update model, so
``extra="forbid"`` rejects them. A test is the only thing that keeps someone
from "helpfully" adding them back.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.inventory.schemas import (
    ApplicationCreateRequest,
    ApplicationUpdateRequest,
    HealthCheckConfig,
    ServerCreateRequest,
    ServerUpdateRequest,
)

ACCOUNT = "00000000-0000-0000-0000-0000000000a1"


def _server_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "cloud_account_id": ACCOUNT,
        "provider_resource_id": "i-0123456789abcdef",
        "hostname": "web-01.internal.example",
        "display_name": "Web 01",
        "operating_system": "LINUX",
        "environment": "staging",
        "management_method": "AWS_SSM",
    }
    body.update(overrides)
    return body


def _locations(error: ValidationError) -> set[tuple[str, ...]]:
    return {tuple(str(part) for part in item["loc"]) for item in error.errors()}


class TestServerCreateRequest:
    def test_a_minimal_body_is_accepted(self) -> None:
        request = ServerCreateRequest.model_validate(_server_body())
        assert request.monitoring_enabled is True
        assert request.application_ids == []

    @pytest.mark.parametrize(
        "hostname",
        [
            "-leading-hyphen.example",
            "trailing-hyphen-.example",
            "double..dot",
            "has space.example",
            "under_score.example",
            "a" * 254,
        ],
    )
    def test_an_invalid_hostname_is_rejected(self, hostname: str) -> None:
        with pytest.raises(ValidationError) as caught:
            ServerCreateRequest.model_validate(_server_body(hostname=hostname))
        assert ("hostname",) in _locations(caught.value)

    @pytest.mark.parametrize("hostname", ["a", "web-01.internal.example", "host.", "A-B.example"])
    def test_a_valid_hostname_is_accepted(self, hostname: str) -> None:
        assert ServerCreateRequest.model_validate(_server_body(hostname=hostname))

    @pytest.mark.parametrize("value", ["10.0.0.300", "not-an-ip", "10.0.0", "::gg"])
    def test_an_invalid_ip_is_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError) as caught:
            ServerCreateRequest.model_validate(_server_body(private_ip=value))
        assert ("private_ip",) in _locations(caught.value)

    @pytest.mark.parametrize("value", ["10.0.0.10", "fe80::1", "192.168.1.1"])
    def test_a_valid_ip_is_accepted(self, value: str) -> None:
        assert ServerCreateRequest.model_validate(_server_body(private_ip=value))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("operating_system", "SOLARIS"),
            ("environment", "preprod"),
            ("management_method", "SSH"),
        ],
    )
    def test_a_value_outside_a_closed_enum_is_rejected(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError) as caught:
            ServerCreateRequest.model_validate(_server_body(**{field: value}))
        assert (field,) in _locations(caught.value)

    @pytest.mark.parametrize("value", ["host.example:9100", "10.0.0.1:9100", "[fe80::1]:9100"])
    def test_a_valid_metrics_instance_is_accepted(self, value: str) -> None:
        assert ServerCreateRequest.model_validate(_server_body(metrics_instance=value))

    @pytest.mark.parametrize(
        "value", ["host.example", "host.example:", ":9100", "host example:9100", "host:notaport"]
    )
    def test_a_metrics_instance_that_is_not_host_port_is_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError) as caught:
            ServerCreateRequest.model_validate(_server_body(metrics_instance=value))
        assert ("metrics_instance",) in _locations(caught.value)

    def test_status_cannot_be_chosen_at_registration(self) -> None:
        """PRD §4.3: a new server is PENDING, and that is not the client's call."""
        with pytest.raises(ValidationError) as caught:
            ServerCreateRequest.model_validate(_server_body(status="MANAGED"))
        assert ("status",) in _locations(caught.value)


class TestServerUpdateRequest:
    @pytest.mark.parametrize(
        "field",
        ["id", "cloud_account_id", "provider_resource_id", "created_at", "hostname"],
    )
    def test_an_immutable_field_is_rejected(self, field: str) -> None:
        """§14.4. The field is absent from the model, so forbid-extra refuses it."""
        with pytest.raises(ValidationError) as caught:
            ServerUpdateRequest.model_validate({field: "anything"})
        assert (field,) in _locations(caught.value)

    @pytest.mark.parametrize("status", ["STALE", "UNREACHABLE"])
    def test_a_derived_status_cannot_be_set_by_hand(self, status: str) -> None:
        """AD-14: the stale sweep owns these two; an operator owns the others."""
        with pytest.raises(ValidationError):
            ServerUpdateRequest.model_validate({"status": status})

    @pytest.mark.parametrize("status", ["PENDING", "MANAGED", "RETIRED"])
    def test_an_operator_owned_status_is_accepted(self, status: str) -> None:
        assert ServerUpdateRequest.model_validate({"status": status}).status == status

    def test_an_empty_body_is_valid_and_changes_nothing(self) -> None:
        assert ServerUpdateRequest().model_dump(exclude_unset=True) == {}


class TestApplicationRequests:
    # "trailing-" is deliberately absent: the contract's pattern
    # (§18.2, and the ck_applications_slug CHECK) permits a trailing hyphen,
    # and a test asserting otherwise would be testing a stricter rule than the
    # database enforces.
    @pytest.mark.parametrize("slug", ["-leading", "UPPER", "a", "has_underscore", "a" * 64])
    def test_an_invalid_slug_is_rejected(self, slug: str) -> None:
        with pytest.raises(ValidationError) as caught:
            ApplicationCreateRequest.model_validate({"name": "App", "slug": slug})
        assert ("slug",) in _locations(caught.value)

    def test_a_valid_slug_is_accepted(self) -> None:
        assert ApplicationCreateRequest.model_validate({"name": "App", "slug": "checkout-api"})

    def test_slug_is_immutable(self) -> None:
        """It addresses an application in URLs and configuration (it is not the Loki label)."""
        with pytest.raises(ValidationError) as caught:
            ApplicationUpdateRequest.model_validate({"slug": "renamed"})
        assert ("slug",) in _locations(caught.value)

    @pytest.mark.parametrize("value", ["acme/checkout", "acme.org/checkout-api"])
    def test_an_org_repo_reference_is_accepted(self, value: str) -> None:
        body = {"name": "App", "slug": "checkout", "repository_reference": value}
        assert ApplicationCreateRequest.model_validate(body)

    @pytest.mark.parametrize(
        "value",
        [
            "https://github.example/acme/checkout",  # not on the allowlist
            "http://169.254.169.254/latest/meta-data",  # metadata service
            "file:///etc/passwd",
            "ftp://example.test/repo",
            "acme/checkout/extra/parts/that/are/not/org-repo",
        ],
    )
    def test_a_repository_reference_that_is_not_allowlisted_is_rejected(self, value: str) -> None:
        """Mandatory rule 9 and docs/SECURITY.md §19: destinations are registered.

        The test settings configure no ``ssrf_host_allowlist``, so *every* URL
        here is refused — which is the fail-closed default being asserted, not
        an accident of the fixture.
        """
        body = {"name": "App", "slug": "checkout", "repository_reference": value}
        with pytest.raises(ValidationError) as caught:
            ApplicationCreateRequest.model_validate(body)
        assert ("repository_reference",) in _locations(caught.value)

    def test_a_health_check_url_is_allowlist_checked(self) -> None:
        with pytest.raises(ValidationError) as caught:
            HealthCheckConfig.model_validate(
                {
                    "checks": [
                        {
                            "name": "api",
                            "url": "https://internal.example/health",
                            "environment": "staging",
                        }
                    ]
                }
            )
        assert ("checks", "0", "url") in _locations(caught.value)

    def test_a_health_check_url_on_the_allowlist_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings

        monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "health.example")
        get_settings.cache_clear()
        try:
            config = HealthCheckConfig.model_validate(
                {
                    "checks": [
                        {
                            "name": "api",
                            "url": "https://health.example/healthz",
                            "environment": "production",
                        }
                    ]
                }
            )
            assert config.checks[0].expected_status == 200
        finally:
            get_settings.cache_clear()

    def test_http_is_refused_even_for_an_allowlisted_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§19 rule 3: plain http needs its own explicit opt-in."""
        from app.core.config import get_settings

        monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "health.example")
        get_settings.cache_clear()
        try:
            with pytest.raises(ValidationError):
                HealthCheckConfig.model_validate(
                    {
                        "checks": [
                            {
                                "name": "api",
                                "url": "http://health.example/healthz",
                                "environment": "production",
                            }
                        ]
                    }
                )
        finally:
            get_settings.cache_clear()
