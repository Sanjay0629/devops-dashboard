/**
 * Request a deployment of a registered target (API_CONTRACTS.md §21.2; PRD §4.10, §8).
 *
 * **Nothing here authorises anything.** The button that opens it is hidden for usability only; the
 * backend decides every request and decides it again in the worker.
 *
 * The dialog restates what PRD §8 requires before a privileged action — the application, the
 * environment, **the exact pipeline** and the person asking — and the ref being deployed, with the
 * refs the target allows beside it. A production target is visibly different, because "deploy this"
 * on the wrong environment is the mistake the dialog exists to catch. One `Idempotency-Key` is made
 * when the dialog opens, so a double click is one intent and the backend answers the second with
 * the original.
 *
 * The request is **four values**: this target's application and environment, the ref the person
 * types, and a reason. There is no field for a Jenkins URL, a job, a parameter or a node, and the
 * pipeline shown is the registered one — read from the target, never typed.
 *
 * A refusal is said in words and keeps the dialog open: "this needs an approval, which is not
 * available yet" and "that ref is not allowed" are true and useful, and neither is a network error.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { refusalText, requestDeployment, type Requested } from '@/api/deploy';
import type { TargetSummary } from '@/api/deployments';
import { idempotencyKey } from '@/api/inventory';
import { useSession } from '@/auth/useSession';

function problemText(result: Exclude<Requested, { kind: 'ok' }>): string {
  switch (result.kind) {
    case 'refused':
      return refusalText(result);
    case 'forbidden':
      return 'You do not have permission to deploy this application to this environment.';
    case 'not_found':
      return 'This application does not exist, or is outside your access scope.';
    case 'read_only':
      return 'The portal is in read-only mode. State-changing actions are disabled.';
    case 'invalid':
    case 'conflict':
      return result.message;
    case 'unreachable':
      return `Could not reach the backend: ${result.reason}`;
  }
}

export function DeployDialog({
  target,
  onClose,
}: {
  target: TargetSummary;
  onClose: () => void;
}): React.JSX.Element {
  const navigate = useNavigate();
  const { state } = useSession();
  const requester = state.kind === 'authenticated' ? state.session.user.display_name : '';
  // One key per opening of the dialog: re-submitting is the same intent, not a new one.
  const key = useRef(idempotencyKey()).current;
  const [ref, setRef] = useState('');
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

  const production = target.environment === 'production';
  const trimmed = ref.trim();

  const submit = async (): Promise<void> => {
    if (trimmed === '') {
      setProblem('Enter the branch, tag or commit to deploy.');
      return;
    }
    setBusy(true);
    setProblem(null);
    const reasonText = reason.trim();
    const result = await requestDeployment(
      target.application_id,
      target.environment,
      trimmed,
      reasonText === '' ? null : reasonText,
      key,
    );
    setBusy(false);
    if (result.kind === 'ok') {
      void navigate(`/deployments/${result.deployment.deployment_id}`);
      return;
    }
    setProblem(problemText(result));
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="deploy-heading"
      className="mb-4 rounded-lg border p-4 text-sm"
      style={{
        background: 'var(--c-surface)',
        borderColor: production ? 'var(--c-critical)' : 'var(--c-border)',
        borderWidth: production ? 2 : 1,
      }}
    >
      <h2 id="deploy-heading" className="text-base font-semibold">
        Confirm: deploy {target.application_name} to {target.environment}
      </h2>
      {production && (
        <p
          className="mt-2 font-semibold"
          style={{ color: 'var(--c-critical)' }}
          data-testid="production-warning"
        >
          PRODUCTION — this changes a live system.
        </p>
      )}
      <dl className="mt-3">
        {(
          [
            ['Application', target.application_name],
            ['Environment', target.environment],
            ['Pipeline', target.pipeline_reference],
            ['Requested by', requester],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-1">
            <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-4 py-1">
          <dt style={{ color: 'var(--c-text-muted)' }}>Allowed refs</dt>
          <dd>
            {target.allowed_ref_patterns.length === 0
              ? 'None — nothing can be deployed'
              : target.allowed_ref_patterns.map((pattern) => (
                  <code key={pattern} className="ml-2">
                    {pattern}
                  </code>
                ))}
          </dd>
        </div>
      </dl>

      <label className="mt-3 block">
        <span style={{ color: 'var(--c-text-muted)' }}>Branch, tag or commit to deploy</span>
        <input
          type="text"
          value={ref}
          onChange={(event) => {
            setRef(event.target.value);
          }}
          maxLength={200}
          autoComplete="off"
          spellCheck={false}
          className="mt-1 w-full rounded border px-2 py-1 font-mono"
          style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
        />
      </label>
      <label className="mt-3 block">
        <span style={{ color: 'var(--c-text-muted)' }}>Reason (optional)</span>
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

      {target.requires_approval && (
        <p className="mt-2" style={{ color: 'var(--c-warning)' }} data-testid="approval-notice">
          This target needs an approval, and the approval workflow is not available yet, so this
          request will be refused.
        </p>
      )}
      <p className="mt-2" style={{ color: 'var(--c-text-muted)' }}>
        DCP runs the registered pipeline once with the ref above. It does not stop a build once
        Jenkins has started it.
      </p>

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
          {busy ? 'Requesting…' : `Deploy ${target.application_name}`}
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
