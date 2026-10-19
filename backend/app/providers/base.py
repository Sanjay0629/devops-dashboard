"""The provider adapter interface (``OPEN-58``).

Look at the signatures: nothing here takes a string, a dict, ``*args`` or a document name. A
command has no parameter to travel in, and ``tests/providers/test_signatures.py`` fails if one
is added. Synchronous on purpose -- workers and ``boto3`` are.

``execute_operation`` is concrete and final: it checks the capability, then calls the
adapter's ``_submit``. A new adapter therefore cannot forget the gate, and
``__init_subclass__`` refuses an override.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.providers.capabilities import CapabilitySet, ensure_supported
from app.providers.types import (
    AdapterResult,
    ConnectivityResult,
    ExecutionHandle,
    ExecutionStatus,
    ServerInfo,
    ServerRef,
    ServiceAction,
    ServiceRef,
    ServiceSnapshot,
)


class ProviderAdapter(ABC):
    #: ``^[a-z][a-z0-9_]{0,31}$``; stamped on every result and handle this adapter produces.
    adapter_id: ClassVar[str]
    #: ``True`` only for the demonstration adapter (AD-15); read by the API to flag responses.
    demo: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "execute_operation" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} overrides execute_operation; implement _submit instead -- "
                "the capability gate lives in the base class"
            )

    @abstractmethod
    def capabilities(self) -> CapabilitySet: ...

    @abstractmethod
    def get_server_info(self, ref: ServerRef) -> AdapterResult[ServerInfo]: ...

    @abstractmethod
    def check_connectivity(self, ref: ServerRef) -> AdapterResult[ConnectivityResult]: ...

    @abstractmethod
    def list_managed_services(self, ref: ServerRef) -> AdapterResult[list[ServiceSnapshot]]: ...

    @abstractmethod
    def _submit(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        """Invoke the provider's registered artifact. Never blind-retried by the caller."""

    @abstractmethod
    def get_operation_status(self, handle: ExecutionHandle) -> AdapterResult[ExecutionStatus]: ...

    def execute_operation(
        self, ref: ServerRef, service: ServiceRef, action: ServiceAction, *, operation_id: uuid.UUID
    ) -> ExecutionHandle:
        ensure_supported(self.capabilities(), action, adapter_id=self.adapter_id)
        handle = self._submit(ref, service, action, operation_id=operation_id)
        if handle.adapter_id != self.adapter_id:
            # A handle that names another adapter would be polled through the wrong one.
            raise RuntimeError(
                f"{type(self).__name__} returned a handle stamped for another adapter"
            )
        return handle
