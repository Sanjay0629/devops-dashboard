"""What every adapter must satisfy, as a function (docs/TESTING.md §6).

M4.3 and M4.4 add their adapters to ``test_conformance.ADAPTERS`` and get this for free. A
harness nothing can fail proves nothing, so ``test_conformance`` also runs it against adapters
written to break each rule and requires an ``AssertionError``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from app.providers.base import ProviderAdapter
from app.providers.capabilities import CapabilitySet, Support
from app.providers.errors import NotConfigured, ProviderError, UnsupportedOperation
from app.providers.types import AdapterResult, ServerRef, ServiceAction, ServiceRef
from tests.providers.signatures import violations

SERVER = ServerRef("i-0abc123", "000000000000", "AWS_SSM", "placeholder-region-1")
SERVICE = ServiceRef(uuid.UUID(int=1), "nginx.service")


def _call(adapter: ProviderAdapter, what: str, fn: Callable[[], Any], demo: bool) -> None:
    try:
        result = fn()
    except ProviderError:
        return  # an honest refusal is allowed; whether it is *right* is the adapter's own tests
    except Exception as error:
        raise AssertionError(
            f"{what} leaked a {type(error).__name__}, not a ProviderError"
        ) from error
    assert isinstance(result, AdapterResult), f"{what} did not return an AdapterResult"
    assert result.adapter_id == adapter.adapter_id, f"{what} is stamped for another adapter"
    assert result.demo_mode is demo, f"{what} carries the wrong demo_mode"


def check(adapter: ProviderAdapter, *, demo: bool) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]{0,31}", adapter.adapter_id), "adapter_id is malformed"
    assert violations(type(adapter)) == [], violations(type(adapter))
    caps = adapter.capabilities()
    assert isinstance(caps, CapabilitySet)

    _call(adapter, "get_server_info", lambda: adapter.get_server_info(SERVER), demo)
    _call(adapter, "check_connectivity", lambda: adapter.check_connectivity(SERVER), demo)
    _call(adapter, "list_managed_services", lambda: adapter.list_managed_services(SERVER), demo)

    for action in ServiceAction:
        try:
            handle = adapter.execute_operation(SERVER, SERVICE, action, operation_id=uuid.uuid4())
        except (UnsupportedOperation, NotConfigured) as refusal:
            assert caps.actions[action] is not Support.SUPPORTED, (
                f"{action} is declared supported but refused as {type(refusal).__name__}"
            )
            continue
        except ProviderError:
            continue  # a real provider may throttle or time out; that is not a conformance matter
        except Exception as error:
            raise AssertionError(
                f"execute_operation({action}) leaked a {type(error).__name__}"
            ) from error
        assert caps.actions[action] is Support.SUPPORTED, f"{action} ran although not supported"
        assert handle.adapter_id == adapter.adapter_id
        _call(
            adapter, "get_operation_status", lambda h=handle: adapter.get_operation_status(h), demo
        )
