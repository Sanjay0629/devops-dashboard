/**
 * Start, stop and restart a registered service (API_CONTRACTS.md §17.1; PRD §4.6).
 *
 * **Nothing here authorises anything.** The buttons are the intersection the backend already
 * computed (`allowed_actions`: the service's policy ∩ what this caller may do on its server) and
 * they are hidden for usability only; the backend decides every request, and decides it again in
 * the worker. The panel renders nothing at all when no action is offered.
 *
 * The confirmation dialog restates the four facts PRD §4.6 requires — server, service,
 * environment, action — and a production target is visibly different, because "restart this"
 * on the wrong environment is the mistake the dialog exists to catch. One `Idempotency-Key` is
 * made when the dialog opens, so a double click is one intent and the backend answers the second
 * with the original.
 *
 * A refusal is said in words and keeps the dialog open: "no provider is configured" and "this
 * needs an approval, which is not available yet" are true and useful, and neither is a network
 * error. There is no free-text field except the reason, and nothing in the request can be a
 * command.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { idempotencyKey } from '@/api/inventory';
import {
  REQUESTABLE_ACTIONS,
  refusalText,
  requestOperation,
  type OperationAction,
  type Requested,
} from '@/api/operations';
import type { ServiceDetail } from '@/api/services';
import { useIsReadOnly } from '@/auth/useSession';

const VERB: Record<OperationAction, string> = {
  start: 'Start',
  stop: 'Stop',
  restart: 'Restart',
};

function problemText(result: Exclude<Requested, { kind: 'ok' }>): string {
  switch (result.kind) {
    case 'refused':
      return refusalText(result);
    case 'forbidden':
      return 'You do not have permission to do that.';
    case 'not_found':
      return 'This service does not exist, or is outside your access scope.';
    case 'read_only':
      return 'The portal is in read-only mode. State-changing actions are disabled.';
    case 'invalid':
    case 'conflict':
      return result.message;
    case 'unreachable':
      return `Could not reach the backend: ${result.reason}`;
  }
}

function ConfirmDialog({
  service,
  action,
  onClose,
}: {
  service: ServiceDetail;
  action: OperationAction;
  onClose: () => void;
}): React.JSX.Element {
  const navigate = useNavigate();
  // One key per opening of the dialog: re-submitting is the same intent, not a new one.
  const key = useRef(idempotencyKey()).current;
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Escape closes the dialog, unless a request is in flight. On the document rather than on the
  // dialog: a non-interactive element carries no key handler, and focus may be anywhere.
  useEffect(() => {
    const close = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('keydown', close);
    };
  }, [busy, onClose]);

  const production = service.environment === 'production';
  const reasonRequired = production || service.approval_required;
  const trimmed = reason.trim();

  const submit = async (): Promise<void> => {
    if (reasonRequired && trimmed === '') {
      setProblem('A reason is required for this environment or action.');
      return;
    }
    setBusy(true);
    setProblem(null);
    const result = await requestOperation(
      service.server_id,
      service.service_name,
      action,
      trimmed === '' ? null : trimmed,
      key,
    );
    setBusy(false);
    if (result.kind === 'ok') {
      void navigate(`/operations/${result.operation.operation_id}`);
      return;
    }
    setProblem(problemText(result));
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-heading"
      className="mt-4 rounded-lg border p-4 text-sm"
      style={{
        background: 'var(--c-surface)',
        borderColor: production ? 'var(--c-critical)' : 'var(--c-border)',
        borderWidth: production ? 2 : 1,
      }}
    >
      <h3 id="confirm-heading" className="text-base font-semibold">
        Confirm: {VERB[action].toLowerCase()} {service.service_name}
      </h3>
      {production && (
        <p
          className="mt-2 font-semibold"
          style={{ color: 'var(--c-critical)' }}
          data-testid="production-warning"
        >
          PRODUCTION — this changes a live service.
        </p>
      )}
      <dl className="mt-3">
        {(
          [
            ['Server', service.server_display_name],
            ['Service', service.service_name],
            ['Environment', service.environment],
            ['Action', VERB[action]],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      <label className="mt-3 block">
        <span style={{ color: 'var(--c-text-muted)' }}>
          Reason{reasonRequired ? ' (required)' : ' (optional)'}
        </span>
        <textarea
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
          }}
          maxLength={500}
          rows={2}
          className="mt-1 w-full rounded border px-2 py-1"
          style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
        />
      </label>
      {service.approval_required && (
        <p className="mt-2" style={{ color: 'var(--c-warning)' }}>
          This operation needs an approval. The approval workflow is not available yet, so the
          request will be refused.
        </p>
      )}
      {problem !== null && (
        <p role="alert" className="mt-3" style={{ color: 'var(--c-critical)' }}>
          {problem}
        </p>
      )}
      <div className="mt-4 flex gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => void submit()}
          className="rounded border px-3 py-1 font-medium"
          style={{ borderColor: production ? 'var(--c-critical)' : 'var(--c-border)' }}
        >
          {busy ? 'Requesting…' : `${VERB[action]} ${service.service_name}`}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onClose}
          className="rounded border px-3 py-1"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function Buttons({
  service,
  offered,
}: {
  service: ServiceDetail;
  offered: OperationAction[];
}): React.JSX.Element {
  const readOnly = useIsReadOnly();
  const [chosen, setChosen] = useState<OperationAction | null>(null);

  return (
    <section aria-labelledby="actions-heading" className="mb-4">
      <h2 id="actions-heading" className="mb-2 text-base font-semibold">
        Actions
      </h2>
      <div className="flex flex-wrap gap-2">
        {offered.map((action) => (
          <button
            key={action}
            type="button"
            disabled={readOnly}
            title={readOnly ? 'The portal is in read-only mode.' : undefined}
            onClick={() => {
              setChosen(action);
            }}
            className="rounded border px-3 py-1 text-sm"
            style={{ borderColor: 'var(--c-border)' }}
          >
            {VERB[action]}
          </button>
        ))}
      </div>
      {chosen !== null && (
        <ConfirmDialog
          // A fresh dialog, and so a fresh key, for each choice.
          key={chosen}
          service={service}
          action={chosen}
          onClose={() => {
            setChosen(null);
          }}
        />
      )}
    </section>
  );
}

/** Renders nothing, and reads no session, when no action is offered. */
export function OperationActions({
  service,
}: {
  service: ServiceDetail;
}): React.JSX.Element | null {
  const offered = REQUESTABLE_ACTIONS.filter((action) => service.allowed_actions.includes(action));
  return offered.length === 0 ? null : <Buttons service={service} offered={offered} />;
}
