"""The notification sink (a development aid) does what the smoke test relies on it doing.

It is not product code, but step 18h asserts on what it recorded and flips its modes, so a
sink that silently stopped recording would turn that step into a pass for the wrong reason.
"""

from __future__ import annotations

import importlib.util
import smtplib
import threading
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import pytest

SINK = Path(__file__).resolve().parents[2] / "infra/compose/notification-sink/sink.py"


@pytest.fixture
def sink() -> Iterator[tuple[Any, str, int]]:
    spec = importlib.util.spec_from_file_location("notification_sink", SINK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    http = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Http)
    smtp = module._SmtpServer(("127.0.0.1", 0), module.Smtp)
    for server in (http, smtp):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield module, f"http://127.0.0.1:{http.server_address[1]}", smtp.server_address[1]
    finally:
        http.shutdown()
        smtp.shutdown()
        http.server_close()
        smtp.server_close()


def received(base: str) -> list[dict[str, Any]]:
    return httpx.get(f"{base}/__received").json()["received"]  # type: ignore[no-any-return]


def test_it_records_a_post_and_answers_ok(sink: tuple[Any, str, int]) -> None:
    _, base, _ = sink
    assert httpx.post(f"{base}/hook", json={"text": "hello"}).status_code == 200
    (item,) = received(base)
    assert item["kind"] == "http" and item["path"] == "/hook"
    assert item["body"] == {"text": "hello"} and item["mode"] == "ok"


@pytest.mark.parametrize(
    ("mode", "status"),
    [("unavailable", 503), ("notfound", 404), ("ratelimited", 429), ("redirect", 302)],
)
def test_modes_change_the_answer_and_it_still_records(
    sink: tuple[Any, str, int], mode: str, status: int
) -> None:
    _, base, _ = sink
    assert httpx.post(f"{base}/__mode", json={"mode": mode}).status_code == 200
    response = httpx.post(f"{base}/hook", json={"n": 1}, follow_redirects=False)
    assert response.status_code == status
    assert len(received(base)) == 1 and received(base)[0]["mode"] == mode
    if mode == "ratelimited":
        assert response.headers["retry-after"] == "1"
    if mode == "redirect":
        assert response.headers["location"].startswith("http://169.254.169.254")


def test_an_unknown_mode_is_refused_and_a_reset_clears(sink: tuple[Any, str, int]) -> None:
    _, base, _ = sink
    assert httpx.post(f"{base}/__mode", json={"mode": "chaos"}).status_code == 422
    httpx.post(f"{base}/hook", json={})
    assert httpx.delete(f"{base}/__received").status_code == 200
    assert received(base) == []


def send_mail(port: int) -> None:
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = "dcp@example.test", "ops@example.test", "hi"
    mail.set_content("body text")
    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
        client.send_message(mail)


def test_it_takes_a_message_over_smtp(sink: tuple[Any, str, int]) -> None:
    _, base, port = sink
    send_mail(port)
    (item,) = received(base)
    assert item["kind"] == "smtp" and "ops@example.test" in item["to"][0]
    assert "body text" in item["data"] and "Subject: hi" in item["data"]


def test_smtp_modes_give_a_transient_and_a_permanent_answer(sink: tuple[Any, str, int]) -> None:
    _, base, port = sink
    httpx.post(f"{base}/__mode", json={"mode": "unavailable"})
    with pytest.raises(smtplib.SMTPRecipientsRefused) as busy:
        send_mail(port)
    assert next(iter(busy.value.recipients.values()))[0] == 451
    httpx.post(f"{base}/__mode", json={"mode": "notfound"})
    with pytest.raises(smtplib.SMTPRecipientsRefused) as gone:
        send_mail(port)
    assert next(iter(gone.value.recipients.values()))[0] == 550
    assert received(base) == []
