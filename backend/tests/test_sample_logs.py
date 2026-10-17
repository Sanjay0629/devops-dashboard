"""The sample log producer (infra/compose/sample-logs/produce.py).

It is a development aid, but M3.1's redaction tests lean on it: "this canary is
absent from Loki" proves nothing if the producer never wrote the canary. These
tests are what make that absence mean something.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

_SOURCE = Path(__file__).parents[2] / "infra" / "compose" / "sample-logs" / "produce.py"


@pytest.fixture(scope="module")
def produce():
    spec = importlib.util.spec_from_file_location("sample_logs_produce", _SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def written(produce, tmp_path: Path) -> Path:
    produce.write_round(tmp_path, 1, datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC))
    return tmp_path


def test_every_source_a_pipeline_must_handle_is_written(produce, written: Path) -> None:
    files = {
        str(p.relative_to(written)).replace("\\", "/") for p in written.rglob("*") if p.is_file()
    }
    assert files == set(produce.render_round(1, datetime.now(UTC)))
    assert {"nginx/access.log", "nginx/error.log", "php-fpm/php-fpm.log", "syslog"} <= files
    assert any(f.startswith("docker/") and f.endswith("-json.log") for f in files)


def test_every_canary_secret_reaches_every_source(produce, written: Path) -> None:
    sources = [p for p in written.rglob("*") if p.is_file()]
    assert len(sources) == 6
    for canary in produce.CANARIES:
        for source in sources:
            text = source.read_text(encoding="utf-8")
            # A docker line is JSON-escaped on the way in; the secrets contain no
            # character that JSON escapes, so a plain substring test is exact.
            assert canary["secret"] in text, f"{canary['name']} missing from {source.name}"


def test_json_sources_parse_and_docker_lines_carry_a_service_attr(written: Path) -> None:
    for line in (written / "app" / "sample-api.log").read_text(encoding="utf-8").splitlines():
        assert "level" in json.loads(line)
    docker_file = next((written / "docker").rglob("*-json.log"))
    services = set()
    for line in docker_file.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        assert entry["stream"] == "stdout" and entry["log"].endswith("\n")
        services.add(entry["attrs"]["service"])
    assert "sample-api" in services and any(" " in s for s in services)  # incl. the hostile one


def test_severities_include_spellings_the_pipeline_must_not_trust(produce, written: Path) -> None:
    levels = {
        json.loads(line)["level"]
        for line in (written / "app" / "sample-api.log").read_text(encoding="utf-8").splitlines()
    }
    assert {"DEBUG", "err", "fatal", "TRACE"} <= levels
    assert any("<script>" in level for level in levels)


def test_high_cardinality_values_stay_in_the_line_and_differ_per_round(produce) -> None:
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    first = produce.render_round(1, now)["app/sample-api.log"][0]
    second = produce.render_round(2, now)["app/sample-api.log"][0]
    for field in ("request_id", "user_id", "trace_id"):
        assert json.loads(first)[field] != json.loads(second)[field]


def test_a_large_file_is_rotated_not_grown(produce, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(produce, "ROTATE_AT_BYTES", 1)
    produce.write_round(tmp_path, 1)
    produce.write_round(tmp_path, 2)
    assert (tmp_path / "nginx" / "access.log.1").exists()
    assert (tmp_path / "nginx" / "access.log").stat().st_size > 0
