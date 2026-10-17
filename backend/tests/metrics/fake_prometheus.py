"""A Prometheus stand-in that answers by **exact query string**.

Keyed on the full rendered query rather than a metric-name fragment on purpose:
the tests build their expectation with ``templates.render`` and so assert, as a
side effect, that the endpoint sent precisely the allowlisted query for that
template, that instance and that window. A request the fake has no answer for
is a ``400`` — the same thing a real Prometheus says to a query it cannot
parse — so an endpoint that sent a different query fails loudly instead of
quietly receiving an empty result.

Not a mock integration in CLAUDE.md §7's sense: nothing here is presented as
operational. M2.1 verified the real queries against a real Prometheus; this
exists to put values and *holes* in front of the code that interprets them,
which a live ``node_exporter`` cannot be made to produce on demand.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import httpx

NOW = 1_760_000_000.0

#: ``(instance, value)``, or ``(instance, value, extra_labels)`` for series that carry
#: more than an instance (a systemd unit has ``name`` and ``state``).
Instant = Sequence[tuple[str, str] | tuple[str, str, Mapping[str, str]]]
Range = Sequence[tuple[str, Sequence[tuple[float, str]]]]


class FakePrometheus:
    def __init__(self, *, now: float = NOW) -> None:
        self.now = now
        self.instant: dict[str, Instant] = {}
        self.ranges: dict[str, Range] = {}
        self.errors: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.queries: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        self.queries.append(query)
        if query in self.errors:
            return self.errors[query](request)
        if request.url.path.endswith("/query_range"):
            if query not in self.ranges:
                return httpx.Response(400, json={"status": "error", "errorType": "bad_data"})
            result = [
                {"metric": {"instance": instance}, "values": [[t, v] for t, v in values]}
                for instance, values in self.ranges[query]
            ]
            return httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "matrix", "result": result}},
            )
        if query not in self.instant:
            return httpx.Response(400, json={"status": "error", "errorType": "bad_data"})
        result = [
            {
                "metric": {"instance": entry[0], **(entry[2] if len(entry) == 3 else {})},
                "value": [self.now, entry[1]],
            }
            for entry in self.instant[query]
        ]
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
        )
