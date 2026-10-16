"""Target discovery and agent observation, without a database (M2.3).

The properties worth pinning:

* **What may be a scrape target** — a policy about ports and addresses that is
  applied twice (at the write boundary and when the file is built), so each
  rejected shape gets its own case. The IPv4-mapped IPv6 form of the cloud
  metadata address is there because a test on the IPv6 form alone would miss it.
* **The file is deterministic, free of free text, and replaced atomically** —
  and a failure while writing leaves the previous file untouched, because an
  empty or half-written file drops every target at once.
* **The agent probe emits three states and refuses to guess a fourth.**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import ConfigurationError, Settings, get_settings
from app.metrics import agents, discovery, templates
from app.metrics.client import CircuitBreaker, PrometheusClient, PrometheusUnavailableError
from tests.metrics.fake_prometheus import NOW, FakePrometheus

PORTS = (9100,)


# --- target policy ---------------------------------------------------------------


@pytest.mark.parametrize(
    "instance",
    ["web1:9100", "node-exporter:9100", "10.0.0.5:9100", "[2001:db8::1]:9100", "a.b.example:9100"],
)
def test_ordinary_exporters_are_permitted(instance: str) -> None:
    assert discovery.target_problem(instance, PORTS) is None


@pytest.mark.parametrize(
    ("instance", "reason"),
    [
        ("web1:9101", "port_not_allowed"),
        ("web1:22", "port_not_allowed"),
        ("10.0.0.5:5432", "port_not_allowed"),
        ("127.0.0.1:9100", "address_not_allowed"),
        ("[::1]:9100", "address_not_allowed"),
        # The cloud metadata endpoint, in every spelling that reaches it.
        ("169.254.169.254:9100", "address_not_allowed"),
        ("[fe80::1]:9100", "address_not_allowed"),
        ("[::ffff:a9fe:a9fe]:9100", "address_not_allowed"),
        ("0.0.0.0:9100", "address_not_allowed"),
        ("224.0.0.1:9100", "address_not_allowed"),
        ("demo-1.demo.invalid:9100", "reserved_domain"),
        ("HOST.INVALID:9100", "reserved_domain"),
        ("nohost", "not_host_port"),
        ("web 1:9100", "not_host_port"),
        # `\Z`, not `$`: a trailing newline must not slip through.
        ("web1:9100\n", "not_host_port"),
        ("", "not_host_port"),
    ],
)
def test_everything_else_is_refused_with_a_reason(instance: str, reason: str) -> None:
    assert discovery.target_problem(instance, PORTS) == reason


def test_the_allowed_ports_are_configurable() -> None:
    assert discovery.target_problem("web1:9558", PORTS) == "port_not_allowed"
    assert discovery.target_problem("web1:9558", (9100, 9558)) is None


def test_a_rejection_never_echoes_the_value() -> None:
    assert "169.254" not in str(discovery.target_problem("169.254.169.254:9100", PORTS))


# --- the file --------------------------------------------------------------------


def targets() -> list[discovery.Target]:
    return [
        discovery.Target("00000000-0000-0000-0000-000000000001", "staging", "a1:9100"),
        discovery.Target("00000000-0000-0000-0000-000000000002", "production", "b1:9100"),
    ]


def test_the_file_is_file_sd_json_with_only_ids_and_an_environment() -> None:
    parsed = json.loads(discovery.render_target_file(targets()))

    assert parsed == [
        {
            "targets": ["a1:9100"],
            "labels": {
                "environment": "staging",
                "server_id": "00000000-0000-0000-0000-000000000001",
            },
        },
        {
            "targets": ["b1:9100"],
            "labels": {
                "environment": "production",
                "server_id": "00000000-0000-0000-0000-000000000002",
            },
        },
    ]
    # No free text can reach a label: only these two keys ever appear.
    assert {key for group in parsed for key in group["labels"]} == {"environment", "server_id"}


def test_the_same_inventory_gives_the_same_bytes() -> None:
    assert discovery.render_target_file(targets()) == discovery.render_target_file(targets())


def test_no_targets_is_an_empty_list_not_an_error() -> None:
    assert json.loads(discovery.render_target_file([])) == []


def test_writing_creates_the_directory_and_reports_a_change(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "node.json"

    assert discovery.write_if_changed(path, "[]\n") is True
    assert path.read_text(encoding="utf-8") == "[]\n"
    # Readable by Prometheus, which runs as another user.
    assert path.stat().st_mode & 0o044 == 0o044 or path.stat().st_mode & 0o004


def test_an_unchanged_file_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "node.json"
    discovery.write_if_changed(path, "[]\n")
    before = path.stat().st_mtime_ns

    assert discovery.write_if_changed(path, "[]\n") is False
    assert path.stat().st_mtime_ns == before


def test_a_changed_file_is_replaced_and_leaves_no_temporary(tmp_path: Path) -> None:
    path = tmp_path / "node.json"
    discovery.write_if_changed(path, "[]\n")

    assert discovery.write_if_changed(path, '[{"targets": []}]\n') is True
    assert path.read_text(encoding="utf-8") == '[{"targets": []}]\n'
    assert [entry.name for entry in tmp_path.iterdir()] == ["node.json"]


def test_a_failure_while_replacing_keeps_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty or half-written file would drop every target at once."""
    path = tmp_path / "node.json"
    discovery.write_if_changed(path, "OLD\n")

    def boom(self: Path, target: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        discovery.write_if_changed(path, "NEW\n")

    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "OLD\n"
    assert [entry.name for entry in tmp_path.iterdir()] == ["node.json"]


# --- settings --------------------------------------------------------------------


def test_the_port_setting_parses_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_METRICS_TARGET_PORTS", "9100, 9558")
    get_settings.cache_clear()
    assert get_settings().metrics_target_ports == (9100, 9558)
    get_settings.cache_clear()


@pytest.mark.parametrize("value", ["", "0", "70000", "abc", "9100,,x"])
def test_a_bad_port_setting_stops_startup(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DCP_METRICS_TARGET_PORTS", value)
    get_settings.cache_clear()
    with pytest.raises(ConfigurationError, match="DCP_METRICS_TARGET_PORTS"):
        get_settings()
    get_settings.cache_clear()


def test_the_unreachable_window_has_a_floor() -> None:
    """Below two scrapes, one lost scrape would flip a healthy host."""
    with pytest.raises(ValueError):
        Settings(agent_unreachable_after_seconds=15)  # type: ignore[call-arg]


# --- the window query ------------------------------------------------------------


def test_the_window_query_wraps_agent_up() -> None:
    assert templates.render_agent_window("LINUX", ["a1:9100"], 60) == (
        'max_over_time(up{job="node",instance="a1:9100"}[60s])'
    )


def test_the_window_query_refuses_a_nonsense_window_and_a_windows_host() -> None:
    with pytest.raises(templates.InvalidInstanceError):
        templates.render_agent_window("LINUX", ["a1:9100"], 0)
    with pytest.raises(templates.UnsupportedOsFamilyError):
        templates.render_agent_window("WINDOWS", ["a1:9100"], 60)


# --- observing agents ------------------------------------------------------------

URL = "http://prometheus.invalid:9090"
WINDOW = 60


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_PROMETHEUS_URL", URL)
    get_settings.cache_clear()


def client_for(fake: FakePrometheus) -> PrometheusClient:
    return PrometheusClient(
        settings=get_settings(), transport=fake.transport(), circuit=CircuitBreaker()
    )


def script(
    fake: FakePrometheus,
    instances: list[str],
    *,
    window: dict[str, str],
    up: dict[str, str],
    scraped: dict[str, float],
) -> None:
    fake.instant[templates.render_agent_window("LINUX", instances, WINDOW)] = list(window.items())
    fake.instant[templates.render("agent_up", "LINUX", instances)] = list(up.items())
    fake.instant[templates.render_scrape_timestamp("LINUX", instances)] = [
        (instance, str(moment)) for instance, moment in scraped.items()
    ]


async def test_three_hosts_three_states() -> None:
    fake = FakePrometheus()
    hosts = ["a1:9100", "b1:9100", "c1:9100"]
    script(
        fake,
        hosts,
        # c1 has no series at all.
        window={"a1:9100": "1", "b1:9100": "0"},
        up={"a1:9100": "1", "b1:9100": "0"},
        scraped={"a1:9100": NOW - 4, "b1:9100": NOW - 3},
    )

    result = await agents.observe(client_for(fake), hosts, WINDOW)

    assert result["a1:9100"] == agents.Observation(
        "HEALTHY", datetime.fromtimestamp(NOW - 4, tz=UTC)
    )
    # Down for the whole window: unreachable, and *not* "seen" just now — the
    # scrape that recorded `up == 0` is not the host being seen.
    assert result["b1:9100"] == agents.Observation("UNREACHABLE", None)
    assert result["c1:9100"] == agents.UNOBSERVED
    assert result["c1:9100"].status == "UNKNOWN"


async def test_one_lost_scrape_does_not_flip_a_healthy_host() -> None:
    """``up`` is 0 right now but 1 inside the window: still healthy."""
    fake = FakePrometheus()
    host = ["a1:9100"]
    script(fake, host, window={"a1:9100": "1"}, up={"a1:9100": "0"}, scraped={"a1:9100": NOW - 2})

    result = await agents.observe(client_for(fake), host, WINDOW)

    # Healthy, but last_seen is not advanced by a scrape that failed.
    assert result["a1:9100"] == agents.Observation("HEALTHY", None)


async def test_degraded_and_not_installed_are_never_emitted() -> None:
    fake = FakePrometheus()
    hosts = ["a1:9100", "b1:9100"]
    script(
        fake,
        hosts,
        window={"a1:9100": "1", "b1:9100": "0"},
        up={"a1:9100": "1", "b1:9100": "0"},
        scraped={"a1:9100": NOW, "b1:9100": NOW},
    )

    result = await agents.observe(client_for(fake), hosts, WINDOW)

    assert {o.status for o in result.values()} <= {"HEALTHY", "UNREACHABLE", "UNKNOWN"}


async def test_a_nan_window_is_unknown_not_unreachable() -> None:
    fake = FakePrometheus()
    host = ["a1:9100"]
    script(fake, host, window={"a1:9100": "NaN"}, up={}, scraped={})

    assert (await agents.observe(client_for(fake), host, WINDOW))["a1:9100"].status == "UNKNOWN"


async def test_a_large_fleet_is_queried_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agents, "BATCH_SIZE", 2)
    fake = FakePrometheus()
    hosts = ["a1:9100", "b1:9100", "c1:9100"]
    for chunk in (hosts[:2], hosts[2:]):
        script(
            fake,
            chunk,
            window=dict.fromkeys(chunk, "1"),
            up=dict.fromkeys(chunk, "1"),
            scraped=dict.fromkeys(chunk, NOW),
        )

    result = await agents.observe(client_for(fake), hosts, WINDOW)

    assert set(result) == set(hosts)
    assert len(fake.queries) == 6  # three queries per batch, two batches


async def test_an_outage_propagates_for_the_caller_to_turn_into_unknown() -> None:
    fake = FakePrometheus()
    # No scripted answers: every query is a 400, which the client reports as
    # an upstream error.
    with pytest.raises(PrometheusUnavailableError):
        await agents.observe(client_for(fake), ["a1:9100"], WINDOW)


# --- asking the worker to rebuild --------------------------------------------------


async def test_requesting_a_rebuild_publishes_one_argument_free_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md §5: a task receives ids, never objects — and this one needs none."""
    from app.workers.celery_app import celery_app

    sent: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        celery_app, "send_task", lambda name, **options: sent.append((name, options))
    )

    await discovery.request_regeneration()

    # `retry=False`: kombu would otherwise retry a dead broker for seconds inside
    # the request that triggered this.
    assert sent == [
        ("dcp.monitoring.regenerate_targets", {"queue": "integrations", "retry": False})
    ]


async def test_a_dead_broker_does_not_fail_the_request_that_already_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.celery_app import celery_app

    def broken(name: str, **options: object) -> None:
        raise ConnectionError("broker is down")

    monkeypatch.setattr(celery_app, "send_task", broken)

    # Returns normally; the periodic rebuild heals the gap.
    assert await discovery.request_regeneration() is None
