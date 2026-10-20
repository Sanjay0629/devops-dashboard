/**
 * The Administrator's editor for approval policies (§25.11 – §25.14; `OPEN-105`).
 *
 * A policy decides **who must approve what, and for how long a request may wait** — so changing
 * one changes a gate. The page says what each field does, and says out loud the two edits that
 * weaken a gate: turning on self-approval (the requester may decide their own request) and moving
 * a policy to another environment. Both are allowed and both are audited; neither is hidden.
 *
 * What it does not offer, because the workflow does not implement it: more than one approval
 * (`required_approvals` is fixed at 1) and a change to what a policy applies to. A policy that a
 * service or an approval still points at cannot be deleted, and the page shows the backend's
 * refusal in words.
 *
 * Permission and read-only mode only decide what is *shown*; the backend answers `403` and `423`
 * on its own account, and each is shown as what it is.
 */

import { useState } from 'react';

import {
  createPolicy,
  deletePolicy,
  listPolicies,
  updatePolicy,
  type Policy,
  type PolicyCreate,
} from '@/api/approvals';
import type { FieldProblem, Result } from '@/api/inventory';
import { useIsReadOnly } from '@/auth/useSession';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';

const ENVIRONMENTS = ['production', 'staging', 'development', 'other', '*'] as const;

interface Values {
  name: string;
  environment: PolicyCreate['environment'];
  appliesTo: PolicyCreate['applies_to'];
  allowSelf: boolean;
  role: string;
  windowMinutes: string;
}

const BLANK: Values = {
  name: '',
  environment: 'production',
  appliesTo: 'SERVICE_OPERATION',
  allowSelf: false,
  role: 'APPROVER',
  windowMinutes: '60',
};

function valuesOf(policy: Policy): Values {
  return {
    name: policy.name,
    environment: policy.environment as Values['environment'],
    appliesTo: policy.applies_to as Values['appliesTo'],
    allowSelf: policy.allow_self_approval,
    role: policy.required_role,
    windowMinutes: String(policy.approval_window_seconds / 60),
  };
}

function problemText(result: Exclude<Result<unknown>, { kind: 'ok' }>): string {
  switch (result.kind) {
    case 'conflict':
      return result.message;
    case 'invalid':
      return result.problems.length > 0
        ? result.problems.map((p) => `${p.field}: ${p.message}`).join(' ')
        : result.message;
    case 'forbidden':
      return 'You do not have permission to change approval policies.';
    case 'not_found':
      return 'That policy no longer exists.';
    case 'read_only':
      return 'The portal is in read-only mode. State-changing actions are disabled.';
    case 'unreachable':
      return `Could not reach the backend: ${result.reason}`;
  }
}

function PolicyForm({
  initial,
  editing,
  busy,
  problems,
  onSubmit,
  onCancel,
}: {
  initial: Values;
  editing: boolean;
  busy: boolean;
  problems: FieldProblem[];
  onSubmit: (values: Values) => void;
  onCancel: () => void;
}): React.JSX.Element {
  const [values, setValues] = useState<Values>(initial);
  const [clientProblem, setClientProblem] = useState<string | null>(null);
  const field = (name: string): string | undefined =>
    problems.find((problem) => problem.field === name)?.message;
  const input = 'w-full rounded border px-2 py-1';
  const style = { borderColor: 'var(--c-border)', background: 'var(--c-surface)' };

  const submit = (event: React.SyntheticEvent): void => {
    event.preventDefault();
    const minutes = Number(values.windowMinutes);
    if (!Number.isFinite(minutes) || minutes < 1 || minutes > 1440) {
      setClientProblem('The approval window is between 1 and 1440 minutes.');
      return;
    }
    setClientProblem(null);
    onSubmit(values);
  };

  return (
    <form
      onSubmit={submit}
      aria-label={editing ? 'Edit policy' : 'New policy'}
      className="mt-4 space-y-3 rounded-lg border p-4 text-sm"
      style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
    >
      <label className="block">
        <span style={{ color: 'var(--c-text-muted)' }}>Name</span>
        <input
          className={input}
          style={style}
          value={values.name}
          maxLength={128}
          required
          onChange={(event) => {
            setValues({ ...values, name: event.target.value });
          }}
        />
        {field('name') !== undefined && <span role="alert">{field('name')}</span>}
      </label>
      <label className="block">
        <span style={{ color: 'var(--c-text-muted)' }}>Applies in environment</span>
        <select
          className={input}
          style={style}
          value={values.environment}
          onChange={(event) => {
            setValues({
              ...values,
              environment: event.target.value as Values['environment'],
            });
          }}
        >
          {ENVIRONMENTS.map((environment) => (
            <option key={environment} value={environment}>
              {environment === '*' ? '* (every environment)' : environment}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span style={{ color: 'var(--c-text-muted)' }}>Applies to</span>
        <select
          className={input}
          style={style}
          value={values.appliesTo}
          disabled={editing}
          onChange={(event) => {
            setValues({ ...values, appliesTo: event.target.value as Values['appliesTo'] });
          }}
        >
          <option value="SERVICE_OPERATION">Service operations</option>
          <option value="DEPLOYMENT">Deployments</option>
        </select>
        {editing && (
          <span className="block" style={{ color: 'var(--c-text-muted)' }}>
            Fixed when the policy is created.
          </span>
        )}
      </label>
      <label className="block">
        <span style={{ color: 'var(--c-text-muted)' }}>Role an approver must hold</span>
        <input
          className={input}
          style={style}
          value={values.role}
          maxLength={32}
          onChange={(event) => {
            setValues({ ...values, role: event.target.value.toUpperCase() });
          }}
        />
        {field('required_role') !== undefined && <span role="alert">{field('required_role')}</span>}
      </label>
      <label className="block">
        <span style={{ color: 'var(--c-text-muted)' }}>Minutes a request may wait</span>
        <input
          className={input}
          style={style}
          inputMode="numeric"
          value={values.windowMinutes}
          onChange={(event) => {
            setValues({ ...values, windowMinutes: event.target.value });
          }}
        />
        {field('approval_window_seconds') !== undefined && (
          <span role="alert">{field('approval_window_seconds')}</span>
        )}
      </label>
      <p style={{ color: 'var(--c-text-muted)' }}>
        One approval is required. A policy that needs more than one is not supported yet, so it
        cannot be saved.
      </p>
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={values.allowSelf}
          onChange={(event) => {
            setValues({ ...values, allowSelf: event.target.checked });
          }}
        />
        <span>
          Allow a requester to approve their own request
          {values.allowSelf && (
            <strong className="block" style={{ color: 'var(--c-warning)' }}>
              This removes the separation between the person who asks and the person who approves.
              The change is recorded in the audit trail.
            </strong>
          )}
        </span>
      </label>
      {clientProblem !== null && (
        <p role="alert" style={{ color: 'var(--c-critical)' }}>
          {clientProblem}
        </p>
      )}
      <div className="flex gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded border px-3 py-1 font-medium"
          style={{ borderColor: 'var(--c-border)' }}
        >
          {busy ? 'Saving…' : editing ? 'Save changes' : 'Create policy'}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="rounded border px-3 py-1"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

export function ApprovalPolicies(): React.JSX.Element {
  const readOnly = useIsReadOnly();
  const { value, reload } = useLatestResponse(() => listPolicies(), []);
  const [editing, setEditing] = useState<Policy | 'new' | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldProblem[]>([]);

  const save = async (values: Values): Promise<void> => {
    setBusy(true);
    setProblem(null);
    setFields([]);
    const minutes = Number(values.windowMinutes);
    const result =
      editing === 'new'
        ? await createPolicy({
            name: values.name.trim(),
            environment: values.environment,
            applies_to: values.appliesTo,
            required_approvals: 1,
            allow_self_approval: values.allowSelf,
            required_role: values.role,
            approval_window_seconds: Math.round(minutes * 60),
          })
        : editing !== null
          ? await updatePolicy(editing.policy_id, {
              name: values.name.trim(),
              environment: values.environment,
              allow_self_approval: values.allowSelf,
              required_role: values.role,
              approval_window_seconds: Math.round(minutes * 60),
            })
          : null;
    setBusy(false);
    if (result === null) return;
    if (result.kind === 'ok') {
      setEditing(null);
      void reload();
      return;
    }
    if (result.kind === 'invalid') setFields(result.problems);
    setProblem(problemText(result));
  };

  const remove = async (policy: Policy): Promise<void> => {
    if (!window.confirm(`Delete the policy “${policy.name}”?`)) return;
    setBusy(true);
    setProblem(null);
    const result = await deletePolicy(policy.policy_id);
    setBusy(false);
    if (result.kind === 'ok') {
      void reload();
      return;
    }
    setProblem(
      result.kind === 'conflict'
        ? 'That policy is still in use — a service’s action policy links it, or an approval request refers to it — so it cannot be deleted.'
        : problemText(result),
    );
  };

  return (
    <section aria-labelledby="policies-heading">
      <h1 id="policies-heading" className="text-xl font-semibold">
        Approval policies
      </h1>
      <p className="mt-1 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        A policy says which requests need an approval, who may approve, and how long a request may
        wait. It takes effect on the next request; a request already waiting keeps its window.
      </p>

      {problem !== null && (
        <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--c-critical)' }}>
          {problem}
        </p>
      )}

      <div className="mt-4">
        {value === null ? (
          <LoadingState what="approval policies" />
        ) : value.kind !== 'ok' ? (
          <ErrorState result={value} onRetry={() => void reload()} />
        ) : value.value.items.length === 0 ? (
          <EmptyState message="No approval policies exist." />
        ) : (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Approval policies</caption>
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th className="py-1 pr-3 font-normal">Name</th>
                <th className="py-1 pr-3 font-normal">Applies to</th>
                <th className="py-1 pr-3 font-normal">Environment</th>
                <th className="py-1 pr-3 font-normal">Role</th>
                <th className="py-1 pr-3 font-normal">Window</th>
                <th className="py-1 pr-3 font-normal">Self-approval</th>
                <th className="py-1 font-normal">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {value.value.items.map((policy) => (
                <tr
                  key={policy.policy_id}
                  className="border-t"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="py-2 pr-3">{policy.name}</td>
                  <td className="py-2 pr-3">
                    {policy.applies_to === 'SERVICE_OPERATION'
                      ? 'Service operations'
                      : 'Deployments'}
                  </td>
                  <td className="py-2 pr-3">{policy.environment}</td>
                  <td className="py-2 pr-3">{policy.required_role}</td>
                  <td className="py-2 pr-3">{String(policy.approval_window_seconds / 60)} min</td>
                  <td
                    className="py-2 pr-3"
                    style={policy.allow_self_approval ? { color: 'var(--c-warning)' } : undefined}
                  >
                    {policy.allow_self_approval ? 'Allowed' : 'Not allowed'}
                  </td>
                  <td className="py-2">
                    <span className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy || readOnly}
                        onClick={() => {
                          setProblem(null);
                          setFields([]);
                          setEditing(policy);
                        }}
                        className="rounded border px-2 py-1"
                        style={{ borderColor: 'var(--c-border)' }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        disabled={busy || readOnly}
                        onClick={() => void remove(policy)}
                        className="rounded border px-2 py-1"
                        style={{ borderColor: 'var(--c-border)' }}
                      >
                        Delete
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editing === null ? (
        <button
          type="button"
          disabled={readOnly}
          onClick={() => {
            setProblem(null);
            setFields([]);
            setEditing('new');
          }}
          className="mt-4 rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          New policy
        </button>
      ) : (
        <PolicyForm
          key={editing === 'new' ? 'new' : editing.policy_id}
          initial={editing === 'new' ? BLANK : valuesOf(editing)}
          editing={editing !== 'new'}
          busy={busy}
          problems={fields}
          onSubmit={(values) => void save(values)}
          onCancel={() => {
            setEditing(null);
          }}
        />
      )}
    </section>
  );
}
