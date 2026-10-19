"""A DEVELOPMENT AID (CLAUDE.md section 7): a notification receiver that is not Slack, Teams or a mail server.

It accepts an HTTP POST on 8080 and an SMTP submission on 2525, records what it was given, and
answers according to a mode a test can flip. Passing against it proves DCP's own behaviour
(what it sends, to where, how it retries); it is **not** evidence that a real Slack workspace,
Teams tenant or mail provider accepts the message.

    GET    /__received   what has arrived, oldest first
    DELETE /__received   forget it
    POST   /__mode       {"mode": "ok" | "unavailable" | "notfound" | "redirect" | "ratelimited"}
    GET    /healthz

Stdlib only, no authentication: it publishes no port to the host and exists only in the
`mocks` profile.
"""

from __future__ import annotations

import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HTTP_PORT = 8080
SMTP_PORT = 2525

_lock = threading.Lock()
_received: list[dict[str, Any]] = []
_mode = {"value": "ok"}

MODES = ("ok", "unavailable", "notfound", "redirect", "ratelimited")


def _record(item: dict[str, Any]) -> None:
    with _lock:
        _received.append(item)


class Http(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send(self, status: int, body: bytes = b"ok", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200)
        elif self.path == "/__received":
            with _lock:
                payload = json.dumps({"mode": _mode["value"], "received": _received}).encode()
            self._send(200, payload, {"Content-Type": "application/json"})
        else:
            self._send(404)

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path == "/__received":
            with _lock:
                _received.clear()
            self._send(200)
        else:
            self._send(404)

    def do_POST(self) -> None:  # noqa: N802
        raw = self._body()
        if self.path == "/__mode":
            requested = json.loads(raw or b"{}").get("mode")
            if requested not in MODES:
                self._send(422, b"unknown mode")
                return
            _mode["value"] = requested
            self._send(200)
            return
        try:
            body: Any = json.loads(raw)
        except ValueError:
            body = None
        mode = _mode["value"]
        _record(
            {
                "kind": "http",
                "path": self.path,
                "host": self.headers.get("Host"),
                "content_type": self.headers.get("Content-Type"),
                "mode": mode,
                "body": body,
            }
        )
        if mode == "unavailable":
            self._send(503)
        elif mode == "notfound":
            self._send(404)
        elif mode == "ratelimited":
            self._send(429, headers={"Retry-After": "1"})
        elif mode == "redirect":
            self._send(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        else:
            self._send(200)


class Smtp(socketserver.StreamRequestHandler):
    """Just enough SMTP to take one message: EHLO, MAIL, RCPT, DATA, QUIT. No STARTTLS."""

    def _reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self._reply("220 notification-sink ESMTP (development aid)")
        sender = ""
        recipients: list[str] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode("utf-8", "replace").strip()
            verb = command.upper()
            if verb.startswith(("EHLO", "HELO")):
                self._reply("250 notification-sink")
            elif verb.startswith("MAIL FROM"):
                sender = command.split(":", 1)[1].strip()
                self._reply("250 ok")
            elif verb.startswith("RCPT TO"):
                if _mode["value"] == "unavailable":
                    self._reply("451 try again later")
                elif _mode["value"] == "notfound":
                    self._reply("550 no such user")
                else:
                    recipients.append(command.split(":", 1)[1].strip())
                    self._reply("250 ok")
            elif verb == "DATA":
                self._reply("354 end with <CRLF>.<CRLF>")
                data: list[str] = []
                while True:
                    row = self.rfile.readline()
                    if not row or row in (b".\r\n", b".\n"):
                        break
                    data.append(row.decode("utf-8", "replace").rstrip("\r\n"))
                _record(
                    {
                        "kind": "smtp",
                        "from": sender,
                        "to": recipients,
                        "data": "\n".join(data),
                        "mode": _mode["value"],
                    }
                )
                recipients = []
                self._reply("250 queued")
            elif verb == "RSET":
                recipients = []
                self._reply("250 ok")
            elif verb == "NOOP":
                self._reply("250 ok")
            elif verb == "QUIT":
                self._reply("221 bye")
                return
            else:
                self._reply("502 not implemented")


class _SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    smtp = _SmtpServer(("0.0.0.0", SMTP_PORT), Smtp)  # noqa: S104 -- container network only
    threading.Thread(target=smtp.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Http).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
