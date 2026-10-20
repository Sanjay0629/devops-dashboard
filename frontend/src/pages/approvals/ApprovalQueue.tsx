/**
 * The approver queue (API_CONTRACTS.md §22; PRD §3.6, §4.6).
 *
 * What this page owes an approver:
 *
 * 1. **The four facts, restated at the moment of the decision** — server, service, environment and
 *    action — in the same words the requester saw, with a production target visibly different.
 *    "Approve" on the wrong thing is the mistake the dialog exists to catch.
 * 2. **Your own request is marked, and the control is disabled — as advice.** The queue says
 *    `self_requested` and the policy's `self_approval_allowed`; when the first is true and the
 *    second is not, the buttons are off and the page says why. That is not the control: the backend
 *    refuses a self-approval on its own account, and the refusal is shown in words if it arrives.
 * 3. **Time left is shown, and an expired request is not a decision you can make.** The window is
 *    the policy's; the backend refuses a late decision with its own `409`.
 * 4. **The last observed state is labelled as what it is** — an observation with an age, not the
 *    pre-flight read the worker makes at execution.
 * 5. **Every refusal is said in words** and leaves the dialog open, because "someone else already
 *    decided" and "you may not approve your own request" are different facts.
 *
 * Nothing here authorises anything; the backend decides every request.
 */

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  decide,
  decisionProblem,
  listApprovals,
  readApproval,
  type ApprovalDecision,
  type ApprovalDetail,
  type ApprovalItem,
} from '@/api/approvals';
import { useIsReadOnly } from '@/auth/useSession';
import { formatInstant } from '@/components/chart/ticks';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';
import { blockedAsOwn, timeLeft } from '@/pages/approvals/queueFormat';

const DECISIONS: readonly { value: ApprovalDecision; label: string }[] = [
  { value: 'PENDING', label: 'Waiting for a decision' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'EXPIRED', label: 'Expired or withdrawn' },
];

const VERB: Record<string, string> = { start: 'Start', stop: 'Stop', restart: 'Restart' };

function actionLabel(action: string): string {
  return VERB[action] ?? action;
}

function Fact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex justify-between gap-4 py-1">
      <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

function DecisionDialog({
  item,
  verb,
  onClose,
  onDecided,
}: {
  item: ApprovalItem;
  verb: 'approve' | 'reject';
  onClose: () => void;
  onDecided: (message: string) => void;
}): React.JSX.Element {
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A ref as well as state: two clicks inside one render must still be one request.
  const sending = useRef(false);
  const readOnly = useIsReadOnly();
  const production = item.target.environment === 'production';
  const approving = verb === 'approve';
  const trimmed = reason.trim();

  useEffect(() => {
    const close = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('keydown', close);
    };
  }, [busy, onClose]);

  const submit = async (): Promise<void> => {
    if (sending.current) return;
    if (!approving && trimmed === '') {
      setProblem('A reason is required to reject a request.');
      return;
    }
    sending.current = true;
    setBusy(true);
    setProblem(null);
    const result = await decide(item.operation_id, verb, trimmed === '' ? null : trimmed);
    sending.current = false;
    setBusy(false);
    if (result.kind === 'ok') {
      onDecided(
        approving
          ? `Approved: ${actionLabel(item.action_type).toLowerCase()} ${item.target.service_name} will now be carried out.`
          : `Rejected: ${actionLabel(item.action_type).toLowerCase()} ${item.target.service_name} will not be carried out.`,
      );
      return;
    }
    setProblem(decisionProblem(result));
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="decision-heading"
      className="mt-4 rounded-lg border p-4 text-sm"
      style={{
        background: 'var(--c-surface)',
        borderColor: production ? 'var(--c-critical)' : 'var(--c-border)',
        borderWidth: production ? 2 : 1,
      }}
    >
      <h3 id="decision-heading" className="text-base font-semibold">
        {approving ? 'Approve' : 'Reject'}: {actionLabel(item.action_type).toLowerCase()}{' '}
        {item.target.service_name}
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
        <Fact label="Server">{item.target.server_display_name}</Fact>
        <Fact label="Service">{item.target.service_name}</Fact>
        <Fact label="Environment">{item.target.environment}</Fact>
        <Fact label="Action">{actionLabel(item.action_type)}</Fact>
        <Fact label="Requested by">{item.requested_by.display_name}</Fact>
      </dl>
      <label className="mt-3 block">
        <span style={{ color: 'var(--c-text-muted)' }}>
          Reason{approving ? ' (optional)' : ' (required)'}
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
      {problem !== null && (
        <p role="alert" className="mt-3" style={{ color: 'var(--c-critical)' }}>
          {problem}
        </p>
      )}
      <div className="mt-4 flex gap-3">
        <button
          type="button"
          disabled={busy || readOnly}
          onClick={() => void submit()}
          className="rounded border px-3 py-1 font-medium"
          style={{ borderColor: production ? 'var(--c-critical)' : 'var(--c-border)' }}
        >
          {busy ? 'Sending…' : approving ? 'Confirm approval' : 'Confirm rejection'}
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

function Detail({
  item,
  onDecided,
  onClose,
}: {
  item: ApprovalItem;
  onDecided: (message: string) => void;
  onClose: () => void;
}): React.JSX.Element {
  const readOnly = useIsReadOnly();
  const { value } = useLatestResponse(() => readApproval(item.approval_id), [item.approval_id]);
  const [chosen, setChosen] = useState<'approve' | 'reject' | null>(null);
  const detail: ApprovalDetail | null = value?.kind === 'ok' ? value.value : null;
  const open = item.decision === 'PENDING' && timeLeft(item.expires_at, Date.now()) !== 'expired';
  const own = blockedAsOwn(item);

  return (
    <section
      aria-labelledby="approval-heading"
      className="mt-6 rounded-lg border p-4 text-sm"
      style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
    >
      <div className="flex items-start justify-between gap-4">
        <h2 id="approval-heading" className="text-base font-semibold">
          {actionLabel(item.action_type)} {item.target.service_name} on{' '}
          {item.target.server_display_name}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded border px-2 py-1"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Close
        </button>
      </div>
      <dl className="mt-3">
        <Fact label="Environment">{item.target.environment}</Fact>
        <Fact label="Requested by">{item.requested_by.display_name}</Fact>
        <Fact label="Reason">{item.reason ?? 'None given'}</Fact>
        <Fact label="Window ends">{formatInstant(new Date(item.expires_at).getTime())}</Fact>
        {detail !== null && (
          <>
            <Fact label="Needs the role">{detail.policy.required_role}</Fact>
            <Fact label="Last observed state">
              {detail.target_state.state}
              {detail.target_state.observed_at !== null
                ? ` — ${formatInstant(new Date(detail.target_state.observed_at).getTime())}`
                : ' — never observed'}
              {detail.target_state.data_state !== 'ok' && ` (${detail.target_state.data_state})`}
            </Fact>
          </>
        )}
        {item.approved_by != null && (
          <Fact label={item.decision === 'APPROVED' ? 'Approved by' : 'Decided by'}>
            {item.approved_by.display_name}
          </Fact>
        )}
      </dl>
      <p className="mt-2" style={{ color: 'var(--c-text-muted)' }}>
        The state above was last observed by monitoring. DCP reads the service again, from the
        provider, immediately before it acts.
      </p>

      {item.self_requested && (
        <p
          role="status"
          className="mt-3 rounded border px-3 py-2"
          style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
        >
          {own
            ? 'You made this request, and the policy does not allow you to approve your own. Another approver has to decide it.'
            : 'You made this request, and the policy allows you to decide it yourself.'}
        </p>
      )}

      {open && chosen === null && (
        <div className="mt-4 flex gap-3">
          <button
            type="button"
            disabled={own || readOnly}
            title={
              readOnly
                ? 'The portal is in read-only mode.'
                : own
                  ? 'You cannot approve your own request.'
                  : undefined
            }
            onClick={() => {
              setChosen('approve');
            }}
            className="rounded border px-3 py-1 font-medium"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Approve
          </button>
          <button
            type="button"
            disabled={own || readOnly}
            title={
              readOnly
                ? 'The portal is in read-only mode.'
                : own
                  ? 'You cannot reject your own request. Cancel it from the operation page instead.'
                  : undefined
            }
            onClick={() => {
              setChosen('reject');
            }}
            className="rounded border px-3 py-1 font-medium"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Reject
          </button>
          <Link to={`/operations/${item.operation_id}`} className="self-center underline">
            Operation page
          </Link>
        </div>
      )}
      {!open && item.decision === 'PENDING' && (
        <p className="mt-3" style={{ color: 'var(--c-text-muted)' }}>
          The approval window has passed. This request will be cancelled and cannot be approved.
        </p>
      )}
      {chosen !== null && (
        <DecisionDialog
          // A fresh dialog for each choice, so a reason typed for one is not carried to the other.
          key={chosen}
          item={item}
          verb={chosen}
          onClose={() => {
            setChosen(null);
          }}
          onDecided={onDecided}
        />
      )}
    </section>
  );
}

export function ApprovalQueue(): React.JSX.Element {
  const [decision, setDecision] = useState<ApprovalDecision>('PENDING');
  const [selected, setSelected] = useState<ApprovalItem | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { value, reload } = useLatestResponse(() => listApprovals(decision), [decision]);

  // The queue ages: ask again every half minute, so a lapsed or decided request leaves it.
  useEffect(() => {
    const timer = setInterval(() => void reload(), 30_000);
    return () => {
      clearInterval(timer);
    };
  }, [reload]);

  return (
    <section aria-labelledby="approvals-heading">
      <h1 id="approvals-heading" className="text-xl font-semibold">
        Approvals
      </h1>
      <p className="mt-1 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        Requests that need a person to approve them before they run. You see the environments you
        can approve in; a request you made yourself is marked.
      </p>

      <label className="mt-4 flex items-center gap-2 text-sm">
        <span style={{ color: 'var(--c-text-muted)' }}>Show</span>
        <select
          value={decision}
          onChange={(event) => {
            setDecision(event.target.value as ApprovalDecision);
            setSelected(null);
          }}
          className="rounded border px-2 py-1"
          style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
        >
          {DECISIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      {notice !== null && (
        <p
          role="status"
          className="mt-3 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-ok)' }}
        >
          {notice}
        </p>
      )}

      <div className="mt-4">
        {value === null ? (
          <LoadingState what="approvals" />
        ) : value.kind !== 'ok' ? (
          <ErrorState result={value} onRetry={() => void reload()} />
        ) : value.value.items.length === 0 ? (
          <EmptyState
            message={
              decision === 'PENDING'
                ? 'Nothing is waiting for your decision.'
                : 'No requests in this state.'
            }
          />
        ) : (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Approval requests</caption>
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th className="py-1 pr-3 font-normal">Requested by</th>
                <th className="py-1 pr-3 font-normal">Target</th>
                <th className="py-1 pr-3 font-normal">Environment</th>
                <th className="py-1 pr-3 font-normal">Action</th>
                <th className="py-1 pr-3 font-normal">Reason</th>
                <th className="py-1 pr-3 font-normal">
                  {decision === 'PENDING' ? 'Time left' : 'Decided'}
                </th>
                <th className="py-1 font-normal">
                  <span className="sr-only">Review</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {value.value.items.map((item) => (
                <tr
                  key={item.approval_id}
                  className="border-t"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="py-2 pr-3">
                    {item.requested_by.display_name}
                    {item.self_requested && (
                      <span
                        className="ml-2 rounded border px-1 text-xs"
                        style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
                      >
                        Your request
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    {item.target.service_name} on {item.target.server_display_name}
                  </td>
                  <td
                    className="py-2 pr-3"
                    style={
                      item.target.environment === 'production'
                        ? { color: 'var(--c-critical)', fontWeight: 600 }
                        : undefined
                    }
                  >
                    {item.target.environment}
                  </td>
                  <td className="py-2 pr-3">{actionLabel(item.action_type)}</td>
                  <td className="py-2 pr-3">{item.reason ?? '—'}</td>
                  <td className="py-2 pr-3">
                    {decision === 'PENDING'
                      ? timeLeft(item.expires_at, Date.now())
                      : item.decided_at != null
                        ? formatInstant(new Date(item.decided_at).getTime())
                        : '—'}
                  </td>
                  <td className="py-2">
                    <button
                      type="button"
                      onClick={() => {
                        setNotice(null);
                        setSelected(item);
                      }}
                      className="rounded border px-2 py-1"
                      style={{ borderColor: 'var(--c-border)' }}
                    >
                      Review
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selected !== null && (
        <Detail
          key={selected.approval_id}
          item={selected}
          onClose={() => {
            setSelected(null);
          }}
          onDecided={(message) => {
            setSelected(null);
            setNotice(message);
            void reload();
          }}
        />
      )}
    </section>
  );
}
