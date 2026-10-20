/**
 * One operation: its progress and its result (API_CONTRACTS.md §17.2, §17.3; PRD §4.6).
 *
 * The page polls at the `poll_after_seconds` the backend names until the operation is terminal,
 * then stops. What it shows, and the care taken over the wording:
 *
 * - **A success is shown with its evidence** — the state read back after the provider finished —
 *   and nothing is called succeeded without it. The backend refuses to write a success without
 *   that evidence; the page shows it so a person can see it.
 * - **`TIMED_OUT` and `reconciliation_required` mean "we do not know", and say so.** The provider
 *   may have acted. The page tells the person to check the service before trying again, because a
 *   confident "failed" there would invite a second restart on top of a first that worked.
 * - A failure is a closed code in words, and the redacted steps the worker recorded. Provider
 *   text never arrives here: the backend never stores it.
 * - Cancel is offered only while the row is cancellable, and a `409` is shown as "a worker got
 *   there first", not retried.
 *
 * Nothing here authorises anything; the backend decides every request.
 */

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  CANCELLABLE,
  TERMINAL,
  approvalReasonText,
  cancelOperation,
  failureText,
  readOperation,
  type OperationDetail,
  type OperationStatus,
} from '@/api/operations';
import { formatInstant } from '@/components/chart/ticks';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';
import { useIsReadOnly } from '@/auth/useSession';

const STATUS_TEXT: Record<OperationStatus, string> = {
  PENDING: 'Waiting for a worker',
  AWAITING_APPROVAL: 'Waiting for approval',
  APPROVED: 'Approved, waiting for a worker',
  RUNNING: 'Running',
  SUCCEEDED: 'Succeeded — verified',
  FAILED: 'Failed',
  TIMED_OUT: 'Timed out — the outcome is unknown',
  CANCELLED: 'Cancelled',
  REJECTED: 'Rejected',
};

function Row({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div
      className="flex justify-between gap-4 border-t py-2"
      style={{ borderColor: 'var(--c-border)' }}
    >
      <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function when(value: string | null | undefined): React.ReactNode {
  return value == null ? (
    <span style={{ color: 'var(--c-text-muted)' }}>—</span>
  ) : (
    formatInstant(new Date(value).getTime())
  );
}

function reasonCodeOf(operation: OperationDetail): string | null {
  const code = (operation.sanitized_result as { reason_code?: unknown }).reason_code;
  return typeof code === 'string' ? code : null;
}

/**
 * Where the approval stands, in words, and who has to act. Shown whenever the operation had an
 * approval request; an operation that never needed one shows nothing.
 */
function ApprovalState({ operation }: { operation: OperationDetail }): React.JSX.Element | null {
  const approval = operation.approval;
  if (approval == null) return null;
  const code = reasonCodeOf(operation);
  let text: React.ReactNode;
  switch (approval.decision) {
    case 'PENDING':
      text = (
        <>
          Waiting for approval. Someone who holds the <strong>{approval.required_role}</strong>{' '}
          role, and who is not the requester unless the policy allows it, has to decide this. The
          request expires {when(approval.expires_at)}, and is cancelled if nobody does.
        </>
      );
      break;
    case 'APPROVED':
      text = (
        <>
          Approved by <strong>{approval.approved_by?.display_name ?? 'an approver'}</strong>{' '}
          {when(approval.decided_at)}.
        </>
      );
      break;
    case 'REJECTED':
      text = (
        <>
          Rejected by <strong>{approval.approved_by?.display_name ?? 'an approver'}</strong>{' '}
          {when(approval.decided_at)}. Nothing was run.
        </>
      );
      break;
    case 'EXPIRED':
      text =
        code === 'approval_expired'
          ? 'The approval window passed with no decision, so the request was cancelled and nothing was run.'
          : 'The request was cancelled before anyone decided it, so its approval was withdrawn.';
      break;
  }
  return (
    <p
      className="mt-3 rounded border px-3 py-2 text-sm"
      style={{ borderColor: 'var(--c-border)' }}
      data-testid="approval-state"
    >
      {text}
    </p>
  );
}

function stepsOf(operation: OperationDetail): string[] {
  const steps = (operation.sanitized_result as { steps?: unknown }).steps;
  return Array.isArray(steps) ? steps.filter((s): s is string => typeof s === 'string') : [];
}

export function OperationDetailPage(): React.JSX.Element {
  const { operationId = '' } = useParams();
  const readOnly = useIsReadOnly();
  const { value, reload } = useLatestResponse(() => readOperation(operationId), [operationId]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const operation = value?.kind === 'ok' ? value.value : null;
  const wait = operation?.poll_after_seconds ?? null;

  // Ask again after the interval the backend named, and never once it is terminal. Keyed on the
  // operation object so each fresh answer re-arms the timer and a stale one never does.
  useEffect(() => {
    if (operation === null || wait === null) return undefined;
    const timer = setTimeout(() => void reload(), wait * 1000);
    return () => {
      clearTimeout(timer);
    };
  }, [operation, wait, reload]);

  if (value === null) return <LoadingState what="this operation" />;
  if (value.kind !== 'ok') return <ErrorState result={value} onRetry={() => void reload()} />;
  const op = value.value;
  const terminal = TERMINAL.includes(op.execution_status);
  const failure = failureText(op.failure_reason);
  const unknownOutcome =
    (op.sanitized_result as { reconciliation_required?: unknown }).reconciliation_required === true;
  const steps = stepsOf(op);

  const cancel = async (): Promise<void> => {
    setBusy(true);
    setProblem(null);
    const result = await cancelOperation(op.operation_id);
    setBusy(false);
    if (result.kind === 'ok') {
      void reload();
      return;
    }
    setProblem(
      result.kind === 'conflict'
        ? 'A worker has already picked this up, so it can no longer be cancelled.'
        : result.kind === 'forbidden'
          ? 'You do not have permission to cancel this operation.'
          : result.kind === 'read_only'
            ? 'The portal is in read-only mode. State-changing actions are disabled.'
            : result.kind === 'not_found'
              ? 'This operation does not exist, or is outside your access scope.'
              : result.kind === 'invalid'
                ? result.message
                : `Could not reach the backend: ${result.reason}`,
    );
    void reload();
  };

  return (
    <section aria-labelledby="operation-heading">
      <p className="mb-2 text-sm">
        <Link
          to={`/servers/${op.target.server_id}/services/${encodeURIComponent(op.target.service_name)}`}
          className="underline"
        >
          ← {op.target.service_name}
        </Link>
      </p>
      <h1 id="operation-heading" className="text-xl font-semibold">
        {op.action_type[0]?.toUpperCase()}
        {op.action_type.slice(1)} {op.target.service_name}
      </h1>

      <p
        role="status"
        className="mt-2 text-sm font-medium"
        style={{
          color:
            op.execution_status === 'SUCCEEDED'
              ? 'var(--c-ok)'
              : terminal
                ? 'var(--c-critical)'
                : 'var(--c-text)',
        }}
      >
        {STATUS_TEXT[op.execution_status]}
      </p>
      {!terminal && (
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          Checking again every {String(op.poll_after_seconds ?? 0)} seconds.
        </p>
      )}

      {op.execution_status === 'SUCCEEDED' && (
        <p className="mt-3 rounded border px-3 py-2 text-sm" style={{ borderColor: 'var(--c-ok)' }}>
          Verified: the service was read back as <strong>{op.observed_state_after}</strong> after
          the provider finished.
        </p>
      )}

      <ApprovalState operation={op} />

      {terminal &&
        op.execution_status !== 'SUCCEEDED' &&
        op.execution_status !== 'CANCELLED' &&
        op.execution_status !== 'REJECTED' && (
          <div
            role="alert"
            className="mt-3 rounded border px-3 py-2 text-sm"
            style={{ borderColor: 'var(--c-critical)' }}
          >
            {failure !== '' && <p>{failure}</p>}
            {op.failure_reason === 'APPROVAL_REQUIRED' && reasonCodeOf(op) !== null && (
              <p className="mt-1">{approvalReasonText(reasonCodeOf(op))}</p>
            )}
            {unknownOutcome && (
              <p className="mt-1 font-medium">
                The provider may have acted. Check the service’s current state before trying again.
              </p>
            )}
          </div>
        )}

      {problem !== null && (
        <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--c-critical)' }}>
          {problem}
        </p>
      )}

      {CANCELLABLE.includes(op.execution_status) && (
        <button
          type="button"
          disabled={busy || readOnly}
          onClick={() => void cancel()}
          className="mt-3 rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Cancel this operation
        </button>
      )}

      <dl className="mt-4 text-sm">
        <Row label="Server">
          <Link to={`/servers/${op.target.server_id}`} className="underline">
            {op.target.server_display_name}
          </Link>
        </Row>
        <Row label="Environment">{op.target.environment}</Row>
        <Row label="Requested by">{op.requested_by.display_name}</Row>
        <Row label="Reason">{op.requested_reason ?? 'None given'}</Row>
        <Row label="Requested at">{when(op.created_at)}</Row>
        <Row label={op.execution_status === 'AWAITING_APPROVAL' ? 'Expires' : 'Deadline'}>
          {when(op.deadline_at)}
        </Row>
        <Row label="Started">{when(op.started_at)}</Row>
        <Row label="Finished">{when(op.completed_at)}</Row>
        <Row label="State before">{op.observed_state_before ?? 'Not observed'}</Row>
        <Row label="State after">
          {op.observed_state_after ?? 'Not observed'}
          {op.execution_status === 'SUCCEEDED' && op.verified_at != null && (
            <> · verified {when(op.verified_at)}</>
          )}
        </Row>
        {op.external_execution_id != null && (
          <Row label="Provider execution">{op.external_execution_id}</Row>
        )}
      </dl>

      {steps.length > 0 && (
        <div className="mt-4 text-sm">
          <h2 className="mb-1 font-medium">Steps recorded</h2>
          <ol className="list-decimal pl-5">
            {steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
