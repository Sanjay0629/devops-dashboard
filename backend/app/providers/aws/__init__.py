"""The AWS Systems Manager adapter and its factory (``OPEN-78`` – ``OPEN-83``)."""

from __future__ import annotations

from app.core.config import get_settings
from app.providers.aws.adapter import AwsSsmAdapter
from app.providers.aws.api import static_credentials_present
from app.providers.aws.errors import ADAPTER_ID
from app.providers.errors import NotConfigured


def build_adapter() -> AwsSsmAdapter:
    """The registry's factory. Refuses, rather than runs, when it would be unsafe or unset."""
    if static_credentials_present():
        # No static keys, ever (SECURITY.md §8): the presence of one is a refusal, not a warning.
        raise NotConfigured(adapter_id=ADAPTER_ID, detail_code="static_credentials_present")
    version = get_settings().aws_ssm_document_version
    if version is None:
        raise NotConfigured(adapter_id=ADAPTER_ID, detail_code="document_version_not_set")
    return AwsSsmAdapter(document_version=version)


__all__ = ["AwsSsmAdapter", "build_adapter"]
