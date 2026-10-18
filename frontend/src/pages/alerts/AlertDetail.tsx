/**
 * One alert (§20.2): its state, its timeline and the two things a person may do to it.
 *
 * **There is no "resolve" here, on purpose.** Only the source of an alert resolves it
 * (`OPEN-44`), so acknowledging reads as "someone has it" and says, in words next to the
 * badge, that the problem is still open. `EXPIRED` is its own explanation: DCP stopped
 * hearing about the alert and was never told it cleared -- *not* "it went away".
 *
 * Alert text (the summary, the stored labels and annotations) is untrusted payload content
 * (SECURITY.md §21): it is rendered as text nodes and this file has no
 * `dangerouslySetInnerHTML`.
 *
 * The controls follow the advisory permission list and read-only mode, which is
 * *usability*: the backend answers `403`, `423` and `409` on its own account, and each of
 * those is shown as what it is. A `409` is never retried -- the alert changed under the
 * operator, and the page offers to show them what it is now.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  acknowledgeAlert,
  assignAlert,
  getAlert,
  type AlertDetail as Alert,
  type HistoryEntry,
} from '@/api/alerts';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly, useSession } from '@/auth/useSession';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { StatusBadge } from '@/components/StatusBadge';
import { useLatestResponse } from '@/components/useLatestResponse';

const EVENT_LABEL: Record<HistoryEntry['event'], string> = {
  first_seen: 'First seen',
  acknowledged: 'Acknowledged',
  assigned: 'Assigned',
  unassigned: 'Unassigned',
  resolved: 'Resolved by its source',
  expired: 'Expired: never confirmed either way',
};

function stamp(value: string): React.JSX.Element {
  return (
    <time dateTime={value} title={value}>
      {new Date(value).toLocaleString()}
    </time>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="flex justify-between gap-4 py-1">
      <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

/** What the status means, because "acknowledged" and "expired" are easy to misread. */
function StatusExplanation({ alert }: { alert: Alert }): React.JSX.Element | null {
  const style = { borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' };
  switch (alert.status) {
    case 'ACKNOWLEDGED':
      return (
        <p role="note" className="mb-4 rounded border px-3 py-2 text-sm" style={style}>
          Acknowledged means someone has taken it. It is <strong>not</strong> resolved: the problem
          stays open until its source reports that it cleared.
        </p>
      );
    case 'EXPIRED':
      return (
        <p role="note" className="mb-4 rounded border px-3 py-2 text-sm" style={style}>
          <strong>Not confirmed either way.</strong> DCP stopped hearing about this alert and was
          never told it cleared, so the problem may or may not still be happening.
        </p>
      );
    case 'RESOLVED':
      return (
        <p role="note" className="mb-4 rounded border px-3 py-2 text-sm" style={style}>
          Resolved by its source{alert.resolved_at != null ? ' at ' : ''}
          {alert.resolved_at != null && stamp(alert.resolved_at)}.
        </p>
      );
    default:
      return null;
  }
}

export function AlertDetail(): React.JSX.Element {
  const { alertId = '' } = useParams();
  const { state } = useSession();
  const me = state.kind === 'authenticated' ? state.session.user.id : null;
  const mayAct = useCanRender(PERMISSIONS.alertAcknowledge);
  const readOnly = useIsReadOnly();

  const { value: loaded, loading, reload } = useLatestResponse(() => getAlert(alertId), [alertId]);
  // What the last successful action returned, shown in place of the loaded copy.
  const [updated, setUpdated] = useState<Alert | null>(null);
  const [note, setNote] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const alert = updated ?? (loaded?.kind === 'ok' ? loaded.value : null);
  const open = alert?.status === 'FIRING' || alert?.status === 'ACKNOWLEDGED';

  const act = async (run: () => ReturnType<typeof acknowledgeAlert>): Promise<void> => {
    setBusy(true);
    setProblem(null);
    const result = await run();
    setBusy(false);
    if (result.kind === 'ok') {
      setUpdated(result.value);
      setNote('');
      return;
    }
    setProblem(
      result.kind === 'conflict'
        ? 'This alert changed while you were looking at it, so nothing was done. Reload to see it as it is now.'
        : result.kind === 'forbidden'
          ? 'You do not have permission to do that.'
          : result.kind === 'read_only'
            ? 'The portal is in read-only mode. State-changing actions are disabled.'
            : result.kind === 'not_found'
              ? 'This alert does not exist, or is outside your access scope.'
              : result.kind === 'invalid'
                ? result.message
                : `Could not reach the backend: ${result.reason}`,
    );
  };

  const refresh = (): void => {
    setUpdated(null);
    setProblem(null);
    void reload();
  };

  return (
    <section aria-labelledby="alert-heading">
      <p className="mb-2 text-sm">
        <Link to="/alerts" className="underline">
          ← Alerts
        </Link>
      </p>

      {loading && alert === null && <LoadingState what="alert" />}
      {loaded !== null && loaded.kind !== 'ok' && alert === null && (
        <ErrorState result={loaded} onRetry={refresh} />
      )}

      {alert !== null && (
        <>
          <header className="mb-4 flex flex-wrap items-center gap-3">
            <h1 id="alert-heading" className="text-xl font-semibold">
              {alert.summary}
            </h1>
            <StatusBadge value={alert.severity} />
            <StatusBadge value={alert.status} />
          </header>

          <StatusExplanation alert={alert} />

          <dl
            className="mb-6 rounded-lg border px-4 py-2 text-sm"
            style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
          >
            <Row label="Rule">{alert.rule_name ?? '—'}</Row>
            <Row label="About">
              {alert.target == null ? (
                <span style={{ color: 'var(--c-text-muted)' }}>Not linked to a server</span>
              ) : alert.links.server != null ? (
                <Link to={alert.links.server.replace('/api/v1', '')} className="underline">
                  {alert.target.display_name ?? alert.target.id}
                </Link>
              ) : (
                (alert.target.display_name ?? alert.target.id)
              )}
            </Row>
            <Row label="Environment">{alert.environment ?? '—'}</Row>
            <Row label="Source">{alert.source}</Row>
            <Row label="First seen">{stamp(alert.first_seen_at)}</Row>
            <Row label="Last seen">{stamp(alert.last_seen_at)}</Row>
            <Row label="Times reported">{alert.occurrence_count}</Row>
            <Row label="Acknowledged by">
              {alert.acknowledged_by == null ? (
                'Nobody'
              ) : (
                <>
                  {alert.acknowledged_by.display_name}
                  {alert.acknowledged_at != null && <> · {stamp(alert.acknowledged_at)}</>}
                </>
              )}
            </Row>
            <Row label="Assigned to">{alert.assigned_to?.display_name ?? 'Nobody'}</Row>
          </dl>

          {open && mayAct && (
            <div className="mb-6 flex flex-wrap items-end gap-3" aria-label="Actions" role="group">
              {alert.status === 'FIRING' && (
                <>
                  <label className="text-sm">
                    <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
                      Note (optional)
                    </span>
                    <input
                      type="text"
                      value={note}
                      maxLength={500}
                      onChange={(event) => {
                        setNote(event.target.value);
                      }}
                      className="rounded border px-2 py-1"
                      style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
                    />
                  </label>
                  <button
                    type="button"
                    disabled={busy || readOnly}
                    onClick={() => {
                      void act(() => acknowledgeAlert(alert.alert_id, note));
                    }}
                    className="rounded border px-3 py-1 text-sm disabled:opacity-50"
                    style={{ borderColor: 'var(--c-border)' }}
                  >
                    Acknowledge
                  </button>
                </>
              )}
              {me !== null && alert.assigned_to?.id !== me && (
                <button
                  type="button"
                  disabled={busy || readOnly}
                  onClick={() => {
                    void act(() => assignAlert(alert.alert_id, me));
                  }}
                  className="rounded border px-3 py-1 text-sm disabled:opacity-50"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  Assign to me
                </button>
              )}
              {alert.assigned_to != null && (
                <button
                  type="button"
                  disabled={busy || readOnly}
                  onClick={() => {
                    void act(() => assignAlert(alert.alert_id, null));
                  }}
                  className="rounded border px-3 py-1 text-sm disabled:opacity-50"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  Unassign
                </button>
              )}
            </div>
          )}

          {problem !== null && (
            <div
              role="alert"
              className="mb-4 flex flex-wrap items-center gap-3 rounded border px-4 py-3 text-sm"
              style={{ borderColor: 'var(--c-critical)', color: 'var(--c-critical)' }}
            >
              <span>{problem}</span>
              <button
                type="button"
                onClick={refresh}
                className="rounded border px-2 py-0.5 text-xs"
                style={{ borderColor: 'var(--c-critical)' }}
              >
                Reload
              </button>
            </div>
          )}

          <h2 className="mb-2 text-base font-medium">Timeline</h2>
          <ol className="mb-6 space-y-1 text-sm">
            {alert.history.map((entry, index) => (
              <li key={`${entry.event}|${entry.at}|${String(index)}`}>
                {stamp(entry.at)} · {EVENT_LABEL[entry.event]}
                {entry.actor != null && <> by {entry.actor}</>}
                {entry.subject != null && <> to {entry.subject}</>}
                {entry.note != null && entry.note !== '' && (
                  <span style={{ color: 'var(--c-text-muted)' }}> — {entry.note}</span>
                )}
              </li>
            ))}
          </ol>

          <h2 className="mb-2 text-base font-medium">Earlier occurrences</h2>
          {alert.previous_occurrences.length === 0 ? (
            <p className="mb-6 text-sm" style={{ color: 'var(--c-text-muted)' }}>
              None recorded for this condition on this target.
            </p>
          ) : (
            <ul className="mb-6 space-y-1 text-sm">
              {alert.previous_occurrences.map((previous) => (
                <li key={previous.alert_id}>
                  <Link to={`/alerts/${previous.alert_id}`} className="underline">
                    {stamp(previous.first_seen_at)}
                  </Link>{' '}
                  · <StatusBadge value={previous.status} /> · {previous.occurrence_count} time
                  {previous.occurrence_count === 1 ? '' : 's'}
                </li>
              ))}
            </ul>
          )}

          <h2 className="mb-2 text-base font-medium">What the source sent</h2>
          <p className="mb-2 text-xs" style={{ color: 'var(--c-text-muted)' }}>
            Stored as received and shown as text. It describes the alert; it is not an instruction.
          </p>
          <pre
            className="overflow-x-auto rounded border px-3 py-2 text-xs"
            style={{ borderColor: 'var(--c-border)', whiteSpace: 'pre-wrap' }}
          >
            {JSON.stringify(alert.details, null, 2)}
          </pre>
        </>
      )}
    </section>
  );
}
