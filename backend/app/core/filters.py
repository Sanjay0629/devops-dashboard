"""Query-parameter allowlisting (docs/API_CONTRACTS.md §6).

§6: "parameter names and sort keys come from a per-endpoint **allowlist**; an
unknown value ⇒ ``422`` naming the allowed set."

Sort keys and enum-valued filters are allowlisted by their ``Literal`` types in
the handler signature, which FastAPI already turns into a ``422`` with the
permitted values. Parameter *names* are not: FastAPI ignores a query parameter
it was not asked for, so ``?enviroment=production`` would silently return the
unfiltered collection — a typo that looks like a working filter is exactly the
failure §6 is written against, and on a scope-filtered endpoint it is the
difference between "no production servers" and "you did not ask".

Hence one function, called by each list endpoint with the names it accepts.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import Request

from app.core.errors import FieldValidationError


class UnknownQueryParameterError(FieldValidationError):
    """``422 validation_failed`` naming the allowed set (§6, §8)."""

    def __init__(self, unknown: Iterable[str], allowed: Iterable[str]) -> None:
        super().__init__(
            [
                {
                    "loc": ["query", name],
                    "code": "unknown_field",
                    "message": "Unknown query parameter.",
                    "allowed": sorted(allowed),
                }
                # The parameter *name* is echoed, which is safe: it is one of
                # the client's own keys and is never a value. The value is not
                # (docs/API_CONTRACTS.md §8).
                for name in sorted(unknown)
            ]
        )


def reject_unknown_query_parameters(request: Request, allowed: frozenset[str]) -> None:
    """Refuse any query parameter this endpoint does not declare."""
    unknown = {name for name in request.query_params if name not in allowed}
    if unknown:
        raise UnknownQueryParameterError(unknown, allowed)
