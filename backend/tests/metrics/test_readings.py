"""Current values and history (§15.1, §15.2), against a scripted Prometheus.

The property under test is the one PRD §4.4 states and CLAUDE.md §5 repeats:
**a reading is a value or a reason, never a zero.** Every case that has no
number asserts ``value is None`` / ``points is None`` *and* names the reason,
and the state is asserted too, because ``not_configured``, ``unsupported``,
``unavailable`` and ``stale`` are four different instructions to an operator.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import FieldValidationError
from app.inventory.models import Server
from app.metrics import readings, templates
from app.metrics.client import CircuitBreaker, PrometheusClient, reset_breaker
from app.metrics.service import QueryLimitExceededError
from tests.metrics.fake_prometheus import NOW, FakePrometheus

INSTANCE = "web1:9100"
URL = "http://prometheus.invalid:9090"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DCP_PROMETHEUS_URL", URL)
    get_settings.cache_clear()
    reset_breaker()
    yield
    get_settings.cache_clear()
    reset_breaker()


def server(**overrides: object) -> Server:
    fields: dict[str, object] = {
        "id": uuid.uuid4(),
        "hostname": "web1.internal.example",
        "display_name": "Web 1",
        "operating_system": "LINUX",
        "environment": "staging",
        "monitoring_enabled": True,
        "metrics_instance": INSTANCE,
    }
    fields.update(overrides)
    return Server(**fields)


def client_for(fake: FakePrometheus) -> PrometheusClient:
    return PrometheusClient(
        settings=get_settings(), transport=fake.transport(), circuit=CircuitBreaker()
    )


def serve(fake: FakePrometheus, template_id: str, value: str, *, age: float = 5.0) -> None:
    """Script one template's value and the scrape that produced it."""
    fake.instant[templates.render(template_id, "LINUX", [INSTANCE])] = [(INSTANCE, value)]
    fake.instant[templates.render_scrape_timestamp("LINUX", [INSTANCE])] = [
        (INSTANCE, str(NOW - age))
    ]


def only(*ids: str) -> list[templates.MetricTemplate]:
    return readings.template_ids_or_error(list(ids))


def server_error(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(503)


def read_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


# --- §15.1 ---------------------------------------------------------------------


async def test_a_current_value_carries_its_scrape_time_and_threshold_state() -> None:
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "85.5", age=5)

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    [reading] = result.metrics
    assert reading.value == 85.5
    assert reading.data_state == "ok"
    assert reading.collected_at == datetime.fromtimestamp(NOW - 5, tz=UTC)
    assert reading.threshold_state == "warning"
    assert result.thresholds == {"cpu_utilisation": {"warning": 80.0, "critical": 90.0}}
    assert result.source == "prometheus"


async def test_a_template_without_a_threshold_has_no_state_and_no_entry() -> None:
    fake = FakePrometheus()
    serve(fake, "load_average", "3.2")

    result = await readings.current(server(), only("load_average"), client=client_for(fake))

    assert result.metrics[0].value == 3.2
    assert result.metrics[0].threshold_state is None
    assert result.thresholds == {}


async def test_a_real_zero_is_reported_as_zero() -> None:
    """The counterpart to everything below: 0 is allowed when 0 is the answer."""
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "0")

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    [reading] = result.metrics
    assert reading.value == 0.0
    assert reading.data_state == "ok"
    assert reading.threshold_state == "normal"


async def test_the_scrape_age_decides_stale_at_exactly_the_bound() -> None:
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "50", age=60)
    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))
    assert result.metrics[0].data_state == "ok"

    serve(fake, "cpu_utilisation", "50", age=61)
    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))
    [over] = result.metrics
    assert over.data_state == "stale"
    # Stale carries no value: a number this old is not presented as current.
    assert over.value is None
    assert over.threshold_state is None
    assert over.stale_after_seconds == 60
    assert over.collected_at == datetime.fromtimestamp(NOW - 61, tz=UTC)


async def test_a_host_with_no_series_is_unavailable_never_zero() -> None:
    fake = FakePrometheus()
    fake.instant[templates.render("cpu_utilisation", "LINUX", [INSTANCE])] = []
    fake.instant[templates.render_scrape_timestamp("LINUX", [INSTANCE])] = []

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    [reading] = result.metrics
    assert reading.value is None
    assert reading.data_state == "unavailable"
    assert reading.unavailable_reason == "no_data"
    assert reading.collected_at is None


async def test_nan_is_unavailable_never_zero() -> None:
    """Prometheus answers ``NaN`` for a rate window with one sample."""
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "NaN")

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    [reading] = result.metrics
    assert reading.value is None
    assert reading.data_state == "unavailable"


async def test_two_samples_for_one_instance_is_not_guessed_at() -> None:
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "10")
    fake.instant[templates.render("cpu_utilisation", "LINUX", [INSTANCE])] = [
        (INSTANCE, "10"),
        (INSTANCE, "90"),
    ]

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    [reading] = result.metrics
    assert reading.value is None
    assert reading.unavailable_reason == "provider_error"


async def test_an_outage_is_a_reason_not_an_exception() -> None:
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "10")
    for query in list(fake.instant):
        fake.errors[query] = server_error

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    [reading] = result.metrics
    assert (reading.value, reading.data_state) == (None, "unavailable")
    assert reading.unavailable_reason == "provider_error"


async def test_a_timeout_names_itself() -> None:
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "10")
    for query in list(fake.instant):
        fake.errors[query] = read_timeout

    result = await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))

    assert result.metrics[0].unavailable_reason == "provider_timeout"


async def test_one_failing_query_does_not_discard_its_siblings() -> None:
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "10")
    serve(fake, "load_average", "1.5")
    fake.errors[templates.render("load_average", "LINUX", [INSTANCE])] = server_error

    result = await readings.current(
        server(), only("cpu_utilisation", "load_average"), client=client_for(fake)
    )

    by_id = {m.template: m for m in result.metrics}
    assert by_id["cpu_utilisation"].value == 10.0
    assert by_id["load_average"].value is None
    assert by_id["load_average"].data_state == "unavailable"


async def test_an_unconfigured_prometheus_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DCP_PROMETHEUS_URL")
    get_settings.cache_clear()

    result = await readings.current(
        server(), only("cpu_utilisation"), client=client_for(FakePrometheus())
    )

    [reading] = result.metrics
    assert (reading.value, reading.data_state) == (None, "not_configured")
    assert reading.unavailable_reason == "prometheus_not_configured"


async def test_a_windows_server_is_unsupported_and_no_query_is_sent() -> None:
    fake = FakePrometheus()

    result = await readings.current(
        server(operating_system="WINDOWS"),
        only("cpu_utilisation", "load_average"),
        client=client_for(fake),
    )

    assert {(m.data_state, m.unavailable_reason, m.value) for m in result.metrics} == {
        ("unsupported", "unsupported_os", None)
    }
    assert fake.queries == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"monitoring_enabled": False}, "monitoring_disabled"),
        ({"metrics_instance": None}, "no_metrics_instance"),
    ],
)
async def test_an_unmonitored_server_is_not_queried(
    overrides: dict[str, object], reason: str
) -> None:
    fake = FakePrometheus()

    result = await readings.current(
        server(**overrides), only("cpu_utilisation"), client=client_for(fake)
    )

    [reading] = result.metrics
    assert (reading.value, reading.data_state) == (None, "not_configured")
    assert reading.unavailable_reason == reason
    assert fake.queries == []


def test_metrics_default_to_all_and_dedupe() -> None:
    assert [t.id for t in readings.template_ids_or_error(None)] == list(templates.TEMPLATE_IDS)
    assert [t.id for t in only("load_average", "load_average")] == ["load_average"]


def test_an_unknown_template_is_a_field_error_not_a_dropped_filter() -> None:
    with pytest.raises(FieldValidationError) as caught:
        readings.template_ids_or_error(["cpu_utilisation", "not_a_template"])
    assert caught.value.details is not None
    assert caught.value.details["fields"][0]["loc"] == ["query", "metrics"]


# --- §15.2 ---------------------------------------------------------------------

START = datetime.fromtimestamp(NOW - 3600, tz=UTC)
END = datetime.fromtimestamp(NOW, tz=UTC)


def range_query(template_id: str, step: int = 900) -> str:
    return templates.render(
        template_id,
        "LINUX",
        [INSTANCE],
        window_seconds=max(templates.DEFAULT_WINDOW_SECONDS, step),
    )


async def history(
    fake: FakePrometheus, *, step: int = 900, srv: Server | None = None, ids: tuple[str, ...] = ()
) -> readings.RangeMetrics:
    return await readings.history(
        srv or server(),
        only(*(ids or ("cpu_utilisation",))),
        start=START,
        end=END,
        step_seconds=step,
        client=client_for(fake),
    )


async def test_holes_in_the_series_come_back_as_null_points_at_their_timestamps() -> None:
    fake = FakePrometheus()
    t0 = NOW - 3600
    fake.ranges[range_query("cpu_utilisation")] = [
        # Samples at k=0, 1 and 3; k=2 and k=4 are absent, and k=3 is NaN.
        (INSTANCE, [(t0, "10"), (t0 + 900, "20"), (t0 + 2700, "NaN")]),
    ]

    result = await history(fake)

    [series] = result.series
    assert series.data_state == "ok"
    assert series.points is not None
    assert [value for _, value in series.points] == [10.0, 20.0, None, None, None]
    # The holes are *at* their timestamps, so a chart can place the gap.
    assert [moment for moment, _ in series.points] == [
        START + timedelta(seconds=900 * k) for k in range(5)
    ]
    assert (result.start, result.end, result.step_seconds) == (START, END, 900)


async def test_a_series_with_every_point_missing_is_unavailable_not_a_flat_line() -> None:
    fake = FakePrometheus()
    fake.ranges[range_query("cpu_utilisation")] = [(INSTANCE, [(NOW - 3600, "NaN")])]

    [series] = (await history(fake)).series

    assert (series.data_state, series.unavailable_reason, series.points) == (
        "unavailable",
        "no_data",
        None,
    )


async def test_no_series_is_unavailable() -> None:
    fake = FakePrometheus()
    fake.ranges[range_query("cpu_utilisation")] = []

    [series] = (await history(fake)).series

    assert series.points is None
    assert series.unavailable_reason == "no_data"


async def test_the_rate_window_widens_with_the_step() -> None:
    """A step wider than the window samples disjoint windows and under-reports."""
    fake = FakePrometheus()
    fake.ranges[range_query("cpu_utilisation", 1800)] = [(INSTANCE, [(NOW - 3600, "5")])]

    await history(fake, step=1800)

    assert "[1800s]" in fake.queries[0]


async def test_a_windows_server_has_unsupported_history_and_no_query_is_sent() -> None:
    fake = FakePrometheus()

    [series] = (await history(fake, srv=server(operating_system="WINDOWS"))).series

    assert (series.data_state, series.unavailable_reason, series.points) == (
        "unsupported",
        "unsupported_os",
        None,
    )
    assert fake.queries == []


async def test_a_range_outage_is_a_reason() -> None:
    fake = FakePrometheus()
    fake.errors[range_query("cpu_utilisation")] = lambda _r: httpx.Response(502)

    [series] = (await history(fake)).series

    assert (series.data_state, series.unavailable_reason) == ("unavailable", "provider_error")


@pytest.mark.parametrize(
    ("start", "end", "step", "error"),
    [
        (END, START, 900, FieldValidationError),
        (START, END, 5, QueryLimitExceededError),
        (START - timedelta(days=31), END, 3600, QueryLimitExceededError),
        (START.replace(tzinfo=None), END, 900, FieldValidationError),
    ],
)
async def test_the_bounds_are_enforced_before_any_query(
    start: datetime, end: datetime, step: int, error: type[Exception]
) -> None:
    fake = FakePrometheus()
    with pytest.raises(error):
        await readings.history(
            server(),
            only("cpu_utilisation"),
            start=start,
            end=end,
            step_seconds=step,
            client=client_for(fake),
        )
    assert fake.queries == []


# --- paths that must not be quietly absorbed --------------------------------------


async def test_a_bug_is_not_mistaken_for_an_outage() -> None:
    """Only ``PrometheusUnavailableError`` becomes a data state.

    ``gather(return_exceptions=True)`` keeps one failed query from discarding its
    siblings; it must not also turn a programming error into "unavailable".
    """
    fake = FakePrometheus()
    serve(fake, "cpu_utilisation", "10")

    def broken(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("a bug, not an outage")

    fake.errors[templates.render("cpu_utilisation", "LINUX", [INSTANCE])] = broken

    with pytest.raises(RuntimeError):
        await readings.current(server(), only("cpu_utilisation"), client=client_for(fake))


async def test_a_family_that_cannot_be_dated_is_not_presented_as_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: an undated number is never shown as current."""
    cpu = templates.BY_ID["cpu_utilisation"]
    undated = dict(templates.BY_ID)
    undated["agent_up"] = templates.MetricTemplate(
        id="agent_up", unit="boolean", description="", queries={}
    )
    monkeypatch.setattr(templates, "BY_ID", undated)
    fake = FakePrometheus()

    result = await readings.current(server(), [cpu], client=client_for(fake))

    [reading] = result.metrics
    assert (reading.value, reading.data_state) == (None, "unsupported")
    assert fake.queries == []


async def test_an_unmonitored_server_has_no_history_and_no_query() -> None:
    fake = FakePrometheus()

    [series] = (await history(fake, srv=server(monitoring_enabled=False))).series

    assert (series.data_state, series.unavailable_reason, series.points) == (
        "not_configured",
        "monitoring_disabled",
        None,
    )
    assert fake.queries == []


async def test_two_range_series_for_one_instance_are_not_guessed_at() -> None:
    fake = FakePrometheus()
    t0 = NOW - 3600
    fake.ranges[range_query("cpu_utilisation")] = [
        (INSTANCE, [(t0, "10")]),
        (INSTANCE, [(t0, "90")]),
    ]

    [series] = (await history(fake)).series

    assert (series.data_state, series.unavailable_reason) == ("unavailable", "provider_error")
