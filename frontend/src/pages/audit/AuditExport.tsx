/**
 * Export a bounded window of the trail (§24.3 – §24.5).
 *
 * What the person needs to know, said before they click and not after:
 *
 * - **It is built in the background** (a few seconds, usually), so the dialog shows the status of
 *   the export it asked for and nothing else.
 * - **The file can be downloaded once.** A second attempt, or another person's, gets nothing. The
 *   button says so, is disabled the moment it is used, and the page does not offer to try again.
 * - **At most 31 days, 100 000 events and 64 MiB.** Over that the export fails and says so; it is
 *   never truncated.
 * - **It expires after 24 hours** if not collected.
 *
 * Polling is of the export's own status (§24.4), which is not an audit read. The request is keyed
 * once per dialog (`Idempotency-Key`), so a second click re-sends the same intent. Everything here
 * is advice: the backend refuses anyone who lacks both `audit:read` and `admin:integrations`.
 */

import { useEffect, useRef, useState } from 'react';

import {
  downloadExport,
  exportFailureText,
  exportStatus,
  requestExport,
  type ExportStatus,
} from '@/api/audit';
import { toInstant } from '@/api/history';
import { idempotencyKey } from '@/api/inventory';
import { useIsReadOnly } from '@/auth/useSession';

const POLL_MS = 2000;

/** Hand a blob to the browser as a file. Returns `false` where the platform cannot. */
function saveBlob(blob: Blob, filename: string): boolean {
  if (typeof URL.createObjectURL !== 'function') return false;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return true;
}

function localValue(date: Date): string {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

type Phase =
  | { kind: 'idle' }
  | { kind: 'requesting' }
  | { kind: 'tracking'; status: ExportStatus | null; id: string }
  | { kind: 'downloaded'; saved: boolean }
  | { kind: 'problem'; message: string };

export function AuditExport(): React.JSX.Element {
  const readOnly = useIsReadOnly();
  const [open, setOpen] = useState(false);
  const now = useRef(new Date());
  const [from, setFrom] = useState(localValue(new Date(now.current.getTime() - 24 * 3600 * 1000)));
  const [to, setTo] = useState(localValue(now.current));
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  // One key per dialog: a double click is one intent, not two exports.
  const key = useRef(idempotencyKey());
  const spent = useRef(false);

  const trackedId = phase.kind === 'tracking' ? phase.id : null;
  const trackedState = phase.kind === 'tracking' ? phase.status?.status : undefined;
  useEffect(() => {
    if (trackedId === null) return;
    if (trackedState === 'READY' || trackedState === 'FAILED') return;
    let cancelled = false;
    const timer = setTimeout(
      () => {
        void exportStatus(trackedId).then((result) => {
          if (cancelled) return;
          if (result.kind === 'ok') {
            setPhase({ kind: 'tracking', id: trackedId, status: result.value });
          } else {
            setPhase({ kind: 'problem', message: 'Could not read the export status.' });
          }
        });
      },
      trackedState === undefined ? 0 : POLL_MS,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [trackedId, trackedState]);

  const start = async (): Promise<void> => {
    const start = toInstant(from);
    const end = toInstant(to);
    if (start === '' || end === '') {
      setPhase({ kind: 'problem', message: 'Choose a start and an end.' });
      return;
    }
    setPhase({ kind: 'requesting' });
    const result = await requestExport(start, end, key.current);
    if (result.kind === 'ok') {
      setPhase({ kind: 'tracking', id: result.accepted.export_id, status: null });
    } else if (result.kind === 'in_progress') {
      setPhase({
        kind: 'problem',
        message: 'You already have an export in progress. Wait for it to finish, then ask again.',
      });
    } else if (result.kind === 'forbidden') {
      setPhase({ kind: 'problem', message: 'You are not allowed to export the audit trail.' });
    } else if (result.kind === 'read_only') {
      setPhase({ kind: 'problem', message: 'The portal is in read-only mode.' });
    } else if (result.kind === 'invalid' || result.kind === 'conflict') {
      setPhase({ kind: 'problem', message: result.message });
    } else {
      setPhase({ kind: 'problem', message: 'The request could not be sent.' });
    }
  };

  const download = async (id: string): Promise<void> => {
    if (spent.current) return; // a double click is one download, never two
    spent.current = true;
    const result = await downloadExport(id);
    if (result.kind === 'ok') {
      setPhase({ kind: 'downloaded', saved: saveBlob(result.blob, result.filename) });
    } else if (result.kind === 'spent') {
      setPhase({
        kind: 'problem',
        message: 'This export was already downloaded, or has expired. Ask for a new one.',
      });
    } else {
      setPhase({
        kind: 'problem',
        message: 'The file could not be downloaded. Ask for a new export.',
      });
    }
  };

  if (!open) {
    return (
      <div className="mt-8">
        <button
          type="button"
          onClick={() => {
            setOpen(true);
          }}
          className="rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Export…
        </button>
      </div>
    );
  }

  const busy = phase.kind === 'requesting' || phase.kind === 'tracking';
  return (
    <section
      aria-labelledby="export-heading"
      className="mt-8 rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
    >
      <h2 id="export-heading" className="mb-1 font-medium">
        Export the audit trail
      </h2>
      <ul className="mb-3 list-disc pl-5" style={{ color: 'var(--c-text-muted)' }}>
        <li>One file of JSON lines, built in the background.</li>
        <li>
          At most 31 days, 100 000 events and 64 MiB. Over that it fails; it is never cut short.
        </li>
        <li>
          <strong>You can download it once.</strong> It is deleted when you do, and after 24 hours
          if you do not.
        </li>
        <li>You only receive events your access scope allows. The download is itself recorded.</li>
      </ul>

      <div className="mb-3 flex flex-wrap items-end gap-3">
        <label>
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            From
          </span>
          <input
            type="datetime-local"
            value={from}
            disabled={busy}
            onChange={(event) => {
              setFrom(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>
        <label>
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            To
          </span>
          <input
            type="datetime-local"
            value={to}
            disabled={busy}
            onChange={(event) => {
              setTo(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>
        <button
          type="button"
          disabled={busy || readOnly || phase.kind === 'downloaded'}
          onClick={() => void start()}
          className="rounded border px-3 py-1"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Request export
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
          }}
          className="rounded border px-3 py-1"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Close
        </button>
      </div>

      <div aria-live="polite">
        {phase.kind === 'requesting' && <p>Requesting…</p>}
        {phase.kind === 'tracking' && phase.status === null && (
          <p>Waiting for the export to start…</p>
        )}
        {phase.kind === 'tracking' && phase.status?.status === 'QUEUED' && <p>Queued…</p>}
        {phase.kind === 'tracking' && phase.status?.status === 'RUNNING' && <p>Building…</p>}
        {phase.kind === 'tracking' && phase.status?.status === 'FAILED' && (
          <p role="alert" style={{ color: 'var(--c-critical)' }}>
            {exportFailureText(phase.status.failure_code)}
          </p>
        )}
        {phase.kind === 'tracking' && phase.status?.status === 'READY' && (
          <div>
            <p>
              Ready: {phase.status.row_count ?? 0} events, {phase.status.size_bytes ?? 0} bytes.
              Expires {new Date(phase.status.expires_at).toLocaleString()}.
            </p>
            <button
              type="button"
              onClick={() => void download(phase.id)}
              className="mt-2 rounded border px-3 py-1"
              style={{ borderColor: 'var(--c-border)' }}
            >
              Download (once)
            </button>
          </div>
        )}
        {phase.kind === 'downloaded' && (
          <p role="status">
            {phase.saved
              ? 'Downloaded. The file has been deleted from the server; ask for a new export if you need another.'
              : 'The file reached the browser but this browser could not save it. It has been deleted from the server; ask for a new export.'}
          </p>
        )}
        {phase.kind === 'problem' && (
          <p role="alert" style={{ color: 'var(--c-critical)' }}>
            {phase.message}
          </p>
        )}
      </div>
    </section>
  );
}
