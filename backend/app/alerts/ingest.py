"""Turn one Alertmanager payload into alert rows, alert by alert (SECURITY.md §21).

Everything here treats the payload as untrusted text. What gives an alert its meaning is
looked up, never read: the **severity** and the **discriminators** come from the rule
catalogue by rule name; the **target and environment** come from rows found by lookup;
the label and annotation text is stored and shown as text and drives nothing. An
``alertname`` the catalogue does not know is rejected, not interpreted, and **no payload
content can select anything to act upon -- this module has no operation to run**: it imports
nothing that can execute a command, make an outbound call or enqueue a task, and a test
pins that.

**One alert, one transaction.** Each alert commits on its own, so one bad alert cannot
roll back the others (API_CONTRACTS.md §20.5) and no ``dedup_key`` lock is held across a
batch (two batches walking overlapping keys in opposite order would deadlock). A *data*
problem with one alert is a per-alert rejection in a ``202``; a *database* problem is
allowed to propagate and become a ``5xx``, because answering ``202`` to an alert that was
not stored would be Alertmanager believing it delivered something it did not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.alerts import store
from app.alerts.catalogue import BY_NAME, WATCHDOG_RULE
from app.alerts.lifecycle import dedup_key
from app.alerts.models import SUMMARY_MAX_LENGTH
from app.alerts.payload import (
    MAX_ALERTS_PER_REQUEST,
    WebhookAlert,
    WebhookEnvelope,
    parse_target_labels,
)
from app.core.logging import get_logger
from app.core.redaction import redact_text, redact_value

logger = get_logger(__name__)

#: Longest ``instance`` text that goes into the key of an *unlinked* alert.
_UNRESOLVED_PREFIX = "unresolved:"


@dataclass(frozen=True, slots=True)
class AlertOutcome:
    index: int
    accepted: bool
    #: ``created``, ``updated``, ``resolved``, ``ignored`` or ``watchdog`` when accepted.
    action: str | None = None
    #: ``malformed``, ``unknown_rule``, ``overflow`` or ``database_rejected`` when not.
    reason: str | None = None


@dataclass(slots=True)
class BatchResult:
    outcomes: list[AlertOutcome] = field(default_factory=list)
    watchdog_seen: bool = False
    #: Alerts Alertmanager itself dropped to honour its ``max_alerts`` (spike finding).
    truncated_by_sender: int = 0

    @property
    def accepted(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.accepted)

    @property
    def rejected(self) -> int:
        return len(self.outcomes) - self.accepted

    def rejections(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            if not outcome.accepted and outcome.reason is not None:
                counts[outcome.reason] = counts.get(outcome.reason, 0) + 1
        return counts


def _apply(session: Session, alert: WebhookAlert, now: datetime) -> str:
    rule = BY_NAME[alert.labels["alertname"]]
    claim = parse_target_labels(alert.labels)
    target = store.resolve_target(session, claim, wants_unit="name" in rule.discriminators)
    target_part = (
        str(target.server_id)
        if target.server_id is not None
        else f"{_UNRESOLVED_PREFIX}{claim.instance or ''}"
    )
    key = dedup_key(
        source="PROMETHEUS",
        rule_name=rule.name,
        target=target_part,
        discriminators={name: alert.labels.get(name, "") for name in rule.discriminators},
    )
    if alert.status == "resolved":
        return store.resolve(session, dedup=key, now=now)

    summary = redact_text(alert.annotations.get("summary") or rule.name)[:SUMMARY_MAX_LENGTH]
    details = redact_value(
        {
            "labels": alert.labels,
            "annotations": alert.annotations,
            "starts_at": alert.startsAt.isoformat(),
            "fingerprint": alert.fingerprint,
        }
    )
    return store.fire(
        session,
        source="PROMETHEUS",
        dedup=key,
        rule_name=rule.name,
        severity=rule.severity,
        target=target,
        summary=summary,
        details=details if isinstance(details, dict) else {},
        now=now,
    )


def process_batch(session: Session, envelope: WebhookEnvelope) -> BatchResult:
    """Apply every alert in the payload, committing each on its own."""
    result = BatchResult(truncated_by_sender=envelope.truncatedAlerts)
    if envelope.truncatedAlerts:
        # Alertmanager dropped these before sending; they are not recoverable here.
        logger.warning("alert_webhook_truncated_by_sender", dropped=envelope.truncatedAlerts)

    overflow = max(0, len(envelope.alerts) - MAX_ALERTS_PER_REQUEST)
    if overflow:
        logger.warning("alert_webhook_overflow", rejected=overflow, cap=MAX_ALERTS_PER_REQUEST)

    for index, raw in enumerate(envelope.alerts):
        if index >= MAX_ALERTS_PER_REQUEST:
            result.outcomes.append(AlertOutcome(index, False, reason="overflow"))
            continue
        try:
            alert = WebhookAlert.model_validate(raw)
        except ValidationError:
            result.outcomes.append(AlertOutcome(index, False, reason="malformed"))
            continue

        name = alert.labels.get("alertname", "")
        if name == WATCHDOG_RULE:
            # Proves the pipeline is alive; carries no alert and stores nothing.
            result.watchdog_seen = True
            result.outcomes.append(AlertOutcome(index, True, action="watchdog"))
            continue
        if name not in BY_NAME:
            result.outcomes.append(AlertOutcome(index, False, reason="unknown_rule"))
            continue

        try:
            action = _apply(session, alert, datetime.now(UTC))
            session.commit()
        except (DataError, IntegrityError):
            session.rollback()
            logger.warning("alert_rejected_by_database", rule=name)
            result.outcomes.append(AlertOutcome(index, False, reason="database_rejected"))
            continue
        except BaseException:
            session.rollback()
            raise
        result.outcomes.append(AlertOutcome(index, True, action=action))
    return result
