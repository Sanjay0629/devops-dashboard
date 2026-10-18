"""A Loki stand-in that **evaluates the LogQL it is sent**.

Not a canned-answer fake: it parses the selector and line filters out of the
rendered query and applies them to a stored set of lines, with the semantics the
M3.2 spike measured on a real Loki 3.3.2 -- ``start`` inclusive, ``end``
exclusive, ``limit`` applied after ordering, ties returned newest-inserted-first
for ``backward``. That makes the paging tests meaningful (a tie group straddling a
page boundary really does straddle it) and makes the query the service builds
part of what is asserted: a selector that was wrong or too wide returns the wrong
lines instead of passing.

It is **not** evidence that a real Loki accepts the query -- the spike did that
once and the M3.2 smoke test does it on every run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx

_MATCHER = re.compile(r'(\w+)(=~|=)"((?:[^"\\]|\\.)*)"')
_FILTER = re.compile(r'\|(=|~) "((?:[^"\\]|\\.)*)"')
_COUNT = re.compile(r"count_over_time\((\{.*?\})\[(\d+)s\]\)")


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


@dataclass
class Line:
    ts: int
    labels: dict[str, str]
    text: str
    seq: int = 0


@dataclass
class FakeLoki:
    lines: list[Line] = field(default_factory=list)
    #: ``(path, query, params)`` of every request, for assertions.
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    #: Answer these paths with this instead (an outage, a timeout, a 500).
    fail: dict[str, Callable[[httpx.Request], httpx.Response]] = field(default_factory=dict)
    now: float = 1_760_000_000.0
    stats_bytes: int | None = 4096
    #: Return lines that share a timestamp in the opposite order. Loki promises no
    #: tie order, so the paging tests run both and the service must not care.
    flip_ties: bool = False

    def add(self, ts: int, text: str, **labels: str) -> None:
        self.lines.append(Line(ts, dict(labels), text, seq=len(self.lines)))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # -- evaluation ------------------------------------------------------------

    @staticmethod
    def _selector(query: str) -> tuple[list[tuple[str, str, str]], str]:
        end = query.index("}")
        matchers = [(n, op, _unescape(v)) for n, op, v in _MATCHER.findall(query[: end + 1])]
        return matchers, query[end + 1 :]

    @staticmethod
    def _labels_match(labels: Mapping[str, str], matchers: list[tuple[str, str, str]]) -> bool:
        for name, op, value in matchers:
            actual = labels.get(name, "")
            if op == "=" and actual != value:
                return False
            if op == "=~" and re.fullmatch(value, actual) is None:
                return False
        return True

    def _select(self, query: str) -> list[Line]:
        matchers, rest = self._selector(query)
        chosen = [ln for ln in self.lines if self._labels_match(ln.labels, matchers)]
        for kind, raw in _FILTER.findall(rest):
            value = _unescape(raw)
            if kind == "=":
                chosen = [ln for ln in chosen if value in ln.text]
            else:
                pattern = re.compile(value)
                chosen = [ln for ln in chosen if pattern.search(ln.text)]
        return chosen

    # -- HTTP ------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        query = params["query"]
        self.requests.append((request.url.path, query, params))
        if request.url.path in self.fail:
            return self.fail[request.url.path](request)
        if request.url.path.endswith("/query_range"):
            return self._range(query, params)
        return self._instant(query, params)

    def _range(self, query: str, params: dict[str, str]) -> httpx.Response:
        start, end, limit = int(params["start"]), int(params["end"]), int(params["limit"])
        backward = params["direction"] == "backward"
        chosen = [ln for ln in self._select(query) if start <= ln.ts < end]
        tie = -1 if self.flip_ties else 1
        chosen.sort(key=lambda ln: (-ln.ts, -tie * ln.seq) if backward else (ln.ts, tie * ln.seq))
        chosen = chosen[:limit]
        streams: dict[tuple[tuple[str, str], ...], list[list[str]]] = {}
        for ln in chosen:
            streams.setdefault(tuple(sorted(ln.labels.items())), []).append([str(ln.ts), ln.text])
        result = [{"stream": dict(key), "values": values} for key, values in streams.items()]
        data: dict[str, object] = {"resultType": "streams", "result": result}
        if self.stats_bytes is not None:
            data["stats"] = {"summary": {"totalBytesProcessed": self.stats_bytes}}
        return httpx.Response(200, json={"status": "success", "data": data})

    def _instant(self, query: str, params: dict[str, str]) -> httpx.Response:
        found = _COUNT.search(query)
        assert found, f"unexpected instant query: {query}"
        window = int(found.group(2))
        at_ns = int(params["time"]) * 10**9
        counts: dict[str, int] = {}
        for ln in self._select(found.group(1)):
            if at_ns - window * 10**9 <= ln.ts <= at_ns:
                server = ln.labels["server_id"]
                counts[server] = counts.get(server, 0) + 1
        result = [
            {"metric": {"server_id": server}, "value": [int(params["time"]), str(count)]}
            for server, count in counts.items()
        ]
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
        )
