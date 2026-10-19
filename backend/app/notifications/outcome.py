"""What a delivery attempt means, and when the next one is (``OPEN-53``).

Pure. A sender reports *facts* (an HTTP status, a refusal, a timeout); this module decides
whether that is success, a transient failure to retry, or a permanent one to stop on, and
names it with a **closed reason code**. The code -- never a URL, a token or the peer's own
words -- is what is stored in ``last_error`` and shown to an operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: A claim is a lease: the row is invisible to other claimants for this long while a send
#: is in flight. A worker that dies mid-send leaves a row that becomes due again.
LEASE_SECONDS: Final = 120
#: Longest a ``Retry-After`` may push the next attempt.
MAX_RETRY_AFTER_SECONDS: Final = 3_600

Kind = Literal["sent", "transient", "permanent"]


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: Kind
    #: ``None`` for ``sent``; otherwise a closed code such as ``http_404`` or ``timeout``.
    reason: str | None = None
    retry_after: float | None = None


SENT: Final = Outcome("sent")

#: Reasons that mean "try again", from senders that raise rather than return a status.
_TRANSIENT_REASONS: Final = frozenset({"timeout", "connect_failed", "dns_failed", "smtp_busy"})


def classify_http(status: int, retry_after: float | None = None) -> Outcome:
    """2xx is delivered; 408/429/5xx are worth another try; everything else is not."""
    if 200 <= status < 300:
        return SENT
    if 300 <= status < 400:
        return Outcome("permanent", "redirect_refused")
    if status in (408, 429) or status >= 500:
        return Outcome("transient", f"http_{status}", retry_after)
    return Outcome("permanent", f"http_{status}")


def classify_reason(reason: str) -> Outcome:
    """An outbound failure by its closed reason: transport trouble retries, a refusal does not."""
    if reason in _TRANSIENT_REASONS:
        return Outcome("transient", reason)
    return Outcome("permanent", reason)


def max_attempts(delays: tuple[int, ...]) -> int:
    """``len(delays)`` retries after the first attempt."""
    return len(delays) + 1


def next_delay(
    delays: tuple[int, ...], attempts_made: int, retry_after: float | None = None
) -> int | None:
    """Seconds until the next attempt after ``attempts_made`` failed ones, or ``None`` to stop.

    ``None`` means the budget is spent (the row is ``ABANDONED``). A ``Retry-After`` can
    lengthen the wait (capped) but never shorten it.
    """
    if attempts_made < 1 or attempts_made > len(delays):
        return None
    scheduled = delays[attempts_made - 1]
    if retry_after is None:
        return scheduled
    return max(scheduled, min(int(retry_after), MAX_RETRY_AFTER_SECONDS))
