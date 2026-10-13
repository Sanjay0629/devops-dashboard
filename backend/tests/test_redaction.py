"""Redaction canaries (docs/TESTING.md §8, mandatory rule 7).

Every case seeds a unique canary string and asserts it does not survive. If a
canary appears anywhere downstream of redaction, that is a release blocker — so
these tests are the thing that makes the control credible rather than decorative.
"""

from __future__ import annotations

import pytest

from app.core.logging import configure_logging
from app.core.redaction import MARKER, redact_text, redact_value, redaction_processor

CANARY = "CANARY-7f3a9b2c-do-not-leak"


@pytest.mark.security
@pytest.mark.parametrize(
    "payload",
    [
        f"Authorization: Bearer {CANARY}",
        f"authorization: basic {CANARY}",
        f"password={CANARY}",
        f'{{"password": "{CANARY}"}}',
        f"client_secret: {CANARY}",
        f"api_key={CANARY}&next=1",
        f"api-key={CANARY}",
        f"access_key={CANARY}",
        f"token={CANARY};path=/",
        f"postgresql://user:{CANARY}@db.internal:5432/app",
        f"redis://default:{CANARY}@cache:6379/0",
        f"-----BEGIN RSA PRIVATE KEY-----\n{CANARY}\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_canary_does_not_survive_redaction(payload: str) -> None:
    scrubbed = redact_text(payload)
    assert CANARY not in scrubbed
    assert MARKER in scrubbed


@pytest.mark.security
def test_jwt_shaped_string_is_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwbGFjZWhvbGRlciJ9.c2lnbmF0dXJlLXBsYWNlaG9sZGVy"
    assert jwt not in redact_text(f"id_token={jwt}")


@pytest.mark.security
def test_aws_access_key_id_is_redacted() -> None:
    assert "AKIA" not in redact_text("key is AKIAIOSFODNN7EXAMPLE here")


@pytest.mark.security
@pytest.mark.parametrize(
    "key", ["password", "api_key", "api-key", "apiKey", "client_secret", "DATABASE_URL"]
)
def test_sensitive_key_drops_value_whatever_its_shape(key: str) -> None:
    """Key-based redaction catches values no pattern would recognise."""
    assert redact_value({key: CANARY}) == {key: MARKER}
    assert redact_value({key: 12345}) == {key: MARKER}
    assert redact_value({key: ["a", "b"]}) == {key: MARKER}


@pytest.mark.security
def test_nested_structures_are_scrubbed() -> None:
    payload = {
        "outer": {"items": [{"secret": CANARY}, {"note": f"token={CANARY}"}]},
        "safe": "ordinary text",
    }
    scrubbed = redact_value(payload)
    assert CANARY not in repr(scrubbed)
    assert scrubbed["safe"] == "ordinary text"


@pytest.mark.security
def test_bytes_are_never_rendered() -> None:
    assert redact_value({"blob": CANARY.encode()}) == {"blob": MARKER}


@pytest.mark.security
def test_marker_leaks_no_length_prefix_or_hash() -> None:
    """Two secrets of very different length redact identically."""
    short = redact_text("password=a")
    long = redact_text("password=" + "a" * 200)
    assert short == long


@pytest.mark.security
def test_deep_nesting_is_capped_not_recursive_forever() -> None:
    payload: dict[str, object] = {"k": "v"}
    for _ in range(40):
        payload = {"k": payload}
    assert MARKER in repr(redact_value(payload))


def test_ordinary_text_is_left_alone() -> None:
    text = "Restarted placeholder-api.service on Placeholder Web 01 at 09:30Z"
    assert redact_text(text) == text


def test_processor_scrubs_the_whole_event() -> None:
    event = {"event": f"login password={CANARY}", "token": CANARY, "status": 200}
    scrubbed = redaction_processor(None, "info", event)
    assert CANARY not in repr(scrubbed)
    assert scrubbed["status"] == 200


@pytest.mark.security
def test_logging_pipeline_scrubs_before_output(capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end: a secret bound to a log call does not reach stdout."""
    configure_logging(level="INFO", json_output=True)
    from app.core.logging import get_logger

    get_logger("test").info("provider_call", password=CANARY, url=f"token={CANARY}")
    captured = capsys.readouterr().out
    assert CANARY not in captured
    assert MARKER in captured
