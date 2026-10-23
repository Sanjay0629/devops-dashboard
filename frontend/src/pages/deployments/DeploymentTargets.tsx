/**
 * Deployment targets (§21.8 – §21.12): the pipelines DCP is allowed to run, and nothing else.
 *
 * What this page owes the reader:
 *
 * 1. **A registered pipeline is not a deployable one.** Nothing deploys in this milestone (there
 *    is no Deploy button, on purpose), and when Jenkins is not configured on this deployment the
 *    page says so above the list rather than leaving a registered target to read as ready.
 * 2. **It never asks for, and cannot send, a Jenkins URL, a node label, a credential or a
 *    command.** The form has a job path and a parameter template; a pasted URL is refused before
 *    it is sent, and the backend refuses it too.
 * 3. **"None" is not "none you may see" is not "could not ask".** An empty list says it may hide
 *    targets outside the caller's scope; a failure is shown as a failure.
 * 4. **A target that needs an approval says so**, without naming the policy: the reader shape does
 *    not carry it.
 *
 * Permission and read-only mode decide only what is *shown*; the backend answers `403` and `423`
 * on its own account.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  SLOT_SOURCES,
  createTarget,
  listTargets,
  looksLikeAUrl,
  parsePatterns,
  readTarget,
  retireTarget,
  updateTarget,
  type ParameterTemplate,
  type SlotSource,
  type TargetDetail,
  type TargetEnvironment,
  type TargetSummary,
} from '@/api/deployments';
import { listApplications, type FieldProblem, type Result } from '@/api/inventory';
import { listPolicies, type Policy } from '@/api/approvals';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { StatusBadge } from '@/components/StatusBadge';
import { useCursorPaging } from '@/components/useCursorPaging';
import { useLatestResponse } from '@/components/useLatestResponse';

const ENVIRONMENTS: readonly TargetEnvironment[] = [
  'development',
  'staging',
  'production',
  'other',
];

interface Row {
  name: string;
  value: string;
}
interface SlotRow {
  name: string;
  source: SlotSource;
}
interface FormValues {
  applicationId: string;
  environment: TargetEnvironment;
  pipeline: string;
  patterns: string;
  fixed: Row[];
  slots: SlotRow[];
  policyId: string;
  rollback: boolean;
  rollbackPipeline: string;
}

const blank: FormValues = {
  applicationId: '',
  environment: 'staging',
  pipeline: '',
  patterns: '',
  fixed: [],
  slots: [{ name: '', source: 'ref' }],
  policyId: '',
  rollback: false,
  rollbackPipeline: '',
};

function valuesOf(target: TargetDetail): FormValues {
  const template = target.parameter_template;
  return {
    applicationId: target.application_id,
    environment: target.environment,
    pipeline: target.pipeline_reference,
    patterns: target.allowed_ref_patterns.join('\n'),
    fixed: Object.entries(template.fixed ?? {}).map(([name, value]) => ({ name, value })),
    slots: Object.entries(template.slots ?? {}).map(([name, source]) => ({ name, source })),
    policyId: target.approval_policy_id ?? '',
    rollback: target.rollback_supported,
    rollbackPipeline: target.rollback_pipeline_reference ?? '',
  };
}

function templateOf(values: FormValues): ParameterTemplate {
  const fixed: Record<string, string> = {};
  for (const row of values.fixed) if (row.name.trim() !== '') fixed[row.name.trim()] = row.value;
  const slots: Record<string, SlotSource> = {};
  for (const row of values.slots) if (row.name.trim() !== '') slots[row.name.trim()] = row.source;
  return { fixed, slots };
}

const input = 'rounded border px-2 py-1 w-full';
const inputStyle = { borderColor: 'var(--c-border)', background: 'var(--c-surface)' };
const muted = { color: 'var(--c-text-muted)' };

function TargetForm({
  initial,
  editing,
  applications,
  policies,
  busy,
  problems,
  onSubmit,
  onCancel,
}: {
  initial: FormValues;
  editing: boolean;
  applications: { id: string; name: string }[];
  policies: Policy[];
  busy: boolean;
  problems: FieldProblem[];
  onSubmit: (values: FormValues) => void;
  onCancel: () => void;
}): React.JSX.Element {
  const [values, setValues] = useState<FormValues>(initial);
  const [clientProblem, setClientProblem] = useState<string | null>(null);
  const field = (name: string): string | undefined =>
    problems.find((problem) => problem.field === name)?.message;
  const applicable = policies.filter(
    (policy) =>
      policy.applies_to === 'DEPLOYMENT' &&
      (policy.environment === values.environment || policy.environment === '*'),
  );

  const submit = (event: React.SyntheticEvent): void => {
    event.preventDefault();
    if (looksLikeAUrl(values.pipeline) || looksLikeAUrl(values.rollbackPipeline)) {
      setClientProblem(
        'That looks like a URL. Enter the Jenkins job path only (for example team/service-deploy); the Jenkins server is configured on the deployment, never here.',
      );
      return;
    }
    setClientProblem(null);
    onSubmit(values);
  };

  return (
    <form
      onSubmit={submit}
      aria-label={editing ? 'Edit deployment target' : 'New deployment target'}
      className="mb-6 space-y-3 rounded-lg border px-4 py-3 text-sm"
      style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
    >
      <label className="block">
        <span className="mb-1 block" style={muted}>
          Application
        </span>
        <select
          required
          disabled={editing}
          value={values.applicationId}
          onChange={(event) => {
            setValues({ ...values, applicationId: event.target.value });
          }}
          className={input}
          style={inputStyle}
        >
          <option value="">Choose an application</option>
          {applications.map((application) => (
            <option key={application.id} value={application.id}>
              {application.name}
            </option>
          ))}
        </select>
        {field('application_id') !== undefined && (
          <span role="alert">{field('application_id')}</span>
        )}
      </label>

      <label className="block">
        <span className="mb-1 block" style={muted}>
          Environment
        </span>
        <select
          disabled={editing}
          value={values.environment}
          onChange={(event) => {
            setValues({
              ...values,
              environment: event.target.value as TargetEnvironment,
              policyId: '',
            });
          }}
          className={input}
          style={inputStyle}
        >
          {ENVIRONMENTS.map((environment) => (
            <option key={environment} value={environment}>
              {environment}
            </option>
          ))}
        </select>
        {editing && (
          <span className="mt-1 block" style={muted}>
            The application and environment cannot be changed. Retire this target and register
            another.
          </span>
        )}
      </label>

      <label className="block">
        <span className="mb-1 block" style={muted}>
          Jenkins job path (for example team/service-deploy)
        </span>
        <input
          required
          maxLength={324}
          value={values.pipeline}
          onChange={(event) => {
            setValues({ ...values, pipeline: event.target.value });
          }}
          className={input}
          style={inputStyle}
          autoComplete="off"
          spellCheck={false}
        />
        <span className="mt-1 block" style={muted}>
          Up to five segments separated by “/”. The Jenkins server itself is configured on the
          deployment and is never entered here.
        </span>
        {clientProblem !== null && <span role="alert">{clientProblem}</span>}
        {field('pipeline_reference') !== undefined && (
          <span role="alert">{field('pipeline_reference')}</span>
        )}
      </label>

      <label className="block">
        <span className="mb-1 block" style={muted}>
          Allowed branches, tags or commits (one pattern per line)
        </span>
        <textarea
          rows={3}
          value={values.patterns}
          onChange={(event) => {
            setValues({ ...values, patterns: event.target.value });
          }}
          className={input}
          style={inputStyle}
          spellCheck={false}
        />
        <span className="mt-1 block" style={muted}>
          Each pattern must match the whole name (for example <code>release/.+</code>). No patterns
          means no branch, tag or commit can be deployed.
        </span>
        {field('allowed_ref_patterns') !== undefined && (
          <span role="alert">{field('allowed_ref_patterns')}</span>
        )}
      </label>

      <fieldset className="space-y-2">
        <legend className="mb-1" style={muted}>
          Build parameters sent to Jenkins
        </legend>
        <p style={muted}>
          Fixed values are sent as written and must not be secrets. A filled parameter is set by DCP
          from the list below; nothing a requester types is sent except the branch, tag or commit,
          after it matches a pattern above.
        </p>
        {values.fixed.map((row, index) => (
          <div key={`fixed-${String(index)}`} className="flex gap-2">
            <input
              aria-label={`Fixed parameter ${String(index + 1)} name`}
              placeholder="NAME"
              value={row.name}
              onChange={(event) => {
                const fixed = values.fixed.map((r, i) =>
                  i === index ? { ...r, name: event.target.value } : r,
                );
                setValues({ ...values, fixed });
              }}
              className={input}
              style={inputStyle}
            />
            <input
              aria-label={`Fixed parameter ${String(index + 1)} value`}
              placeholder="value"
              value={row.value}
              onChange={(event) => {
                const fixed = values.fixed.map((r, i) =>
                  i === index ? { ...r, value: event.target.value } : r,
                );
                setValues({ ...values, fixed });
              }}
              className={input}
              style={inputStyle}
            />
            <button
              type="button"
              aria-label={`Remove fixed parameter ${String(index + 1)}`}
              onClick={() => {
                setValues({ ...values, fixed: values.fixed.filter((_, i) => i !== index) });
              }}
              className="rounded border px-2"
              style={{ borderColor: 'var(--c-border)' }}
            >
              Remove
            </button>
          </div>
        ))}
        {values.slots.map((row, index) => (
          <div key={`slot-${String(index)}`} className="flex gap-2">
            <input
              aria-label={`Filled parameter ${String(index + 1)} name`}
              placeholder="NAME"
              value={row.name}
              onChange={(event) => {
                const slots = values.slots.map((r, i) =>
                  i === index ? { ...r, name: event.target.value } : r,
                );
                setValues({ ...values, slots });
              }}
              className={input}
              style={inputStyle}
            />
            <select
              aria-label={`Filled parameter ${String(index + 1)} source`}
              value={row.source}
              onChange={(event) => {
                const slots = values.slots.map((r, i) =>
                  i === index ? { ...r, source: event.target.value as SlotSource } : r,
                );
                setValues({ ...values, slots });
              }}
              className={input}
              style={inputStyle}
            >
              {SLOT_SOURCES.map((source) => (
                <option key={source.value} value={source.value}>
                  {source.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              aria-label={`Remove filled parameter ${String(index + 1)}`}
              onClick={() => {
                setValues({ ...values, slots: values.slots.filter((_, i) => i !== index) });
              }}
              className="rounded border px-2"
              style={{ borderColor: 'var(--c-border)' }}
            >
              Remove
            </button>
          </div>
        ))}
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => {
              setValues({ ...values, fixed: [...values.fixed, { name: '', value: '' }] });
            }}
            className="rounded border px-3 py-1"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Add fixed parameter
          </button>
          <button
            type="button"
            onClick={() => {
              setValues({ ...values, slots: [...values.slots, { name: '', source: 'ref' }] });
            }}
            className="rounded border px-3 py-1"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Add filled parameter
          </button>
        </div>
        {field('parameter_template') !== undefined && (
          <span role="alert">{field('parameter_template')}</span>
        )}
      </fieldset>

      <label className="block">
        <span className="mb-1 block" style={muted}>
          Approval policy
        </span>
        <select
          value={values.policyId}
          onChange={(event) => {
            setValues({ ...values, policyId: event.target.value });
          }}
          className={input}
          style={inputStyle}
        >
          <option value="">None</option>
          {applicable.map((policy) => (
            <option key={policy.policy_id} value={policy.policy_id}>
              {policy.name}
            </option>
          ))}
        </select>
        {values.environment === 'production' && (
          <span className="mt-1 block" style={muted}>
            A production target must have an approval policy.
          </span>
        )}
        {field('approval_policy_id') !== undefined && (
          <span role="alert">{field('approval_policy_id')}</span>
        )}
      </label>

      <fieldset className="space-y-2">
        <legend className="mb-1" style={muted}>
          Rollback
        </legend>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={values.rollback}
            onChange={(event) => {
              setValues({ ...values, rollback: event.target.checked });
            }}
          />
          This pipeline has a rollback pipeline
        </label>
        {values.rollback && (
          <input
            aria-label="Rollback job path"
            required
            maxLength={324}
            value={values.rollbackPipeline}
            onChange={(event) => {
              setValues({ ...values, rollbackPipeline: event.target.value });
            }}
            className={input}
            style={inputStyle}
            autoComplete="off"
            spellCheck={false}
          />
        )}
        <span className="block" style={muted}>
          DCP never invents a rollback: it is offered only where a rollback pipeline is registered.
        </span>
        {field('rollback_pipeline_reference') !== undefined && (
          <span role="alert">{field('rollback_pipeline_reference')}</span>
        )}
      </fieldset>

      <p style={muted}>Post-deployment health checks cannot be registered yet.</p>

      <div className="flex gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded border px-3 py-1 disabled:opacity-50"
          style={{ borderColor: 'var(--c-border)' }}
        >
          {editing ? 'Save changes' : 'Register target'}
        </button>
        <button
          type="button"
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

function refSummary(target: TargetSummary): React.JSX.Element {
  if (target.allowed_ref_patterns.length === 0) {
    return <span style={muted}>None — nothing can be deployed</span>;
  }
  return (
    <>
      {target.allowed_ref_patterns.map((pattern) => (
        <code key={pattern} className="mr-2">
          {pattern}
        </code>
      ))}
    </>
  );
}

export function DeploymentTargets(): React.JSX.Element {
  const mayManage = useCanRender(PERMISSIONS.adminIntegrations);
  const readOnly = useIsReadOnly();
  const paging = useCursorPaging();
  const {
    value: loaded,
    loading,
    reload,
  } = useLatestResponse(() => listTargets({ cursor: paging.cursor ?? undefined }), [paging.cursor]);
  const [form, setForm] = useState<{ editing: TargetDetail | null } | null>(null);
  const [applications, setApplications] = useState<{ id: string; name: string }[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<FieldProblem[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<string | null>(null);

  useEffect(() => {
    if (!mayManage) return;
    void (async () => {
      const apps = await listApplications({ limit: '200', status: 'ACTIVE' });
      if (apps.kind === 'ok') {
        setApplications(apps.value.items.map((a) => ({ id: a.id, name: a.name })));
      }
      const found = await listPolicies();
      if (found.kind === 'ok') setPolicies(found.value.items);
    })();
  }, [mayManage]);

  const finish = async <T,>(result: Result<T>): Promise<void> => {
    setBusy(false);
    if (result.kind === 'ok') {
      setForm(null);
      setProblems([]);
      setNotice(null);
      setRetiring(null);
      await reload();
      return;
    }
    if (result.kind === 'invalid') {
      setProblems(result.problems);
      setNotice(result.problems.length === 0 ? result.message : null);
      return;
    }
    setNotice(
      result.kind === 'conflict'
        ? 'That conflicts with the current state: a target may already be registered for this application and environment, or it was changed by someone else. Reload and try again.'
        : result.kind === 'forbidden'
          ? 'You do not have permission to do that.'
          : result.kind === 'read_only'
            ? 'The portal is in read-only mode. State-changing actions are disabled.'
            : result.kind === 'not_found'
              ? 'That application or target no longer exists.'
              : `Could not reach the backend: ${result.reason}`,
    );
  };

  const submit = async (values: FormValues): Promise<void> => {
    setBusy(true);
    setProblems([]);
    setNotice(null);
    const editing = form?.editing ?? null;
    const patterns = parsePatterns(values.patterns);
    const rollback = values.rollback ? values.rollbackPipeline.trim() : null;
    if (editing === null) {
      await finish(
        await createTarget({
          application_id: values.applicationId,
          environment: values.environment,
          pipeline_reference: values.pipeline.trim(),
          parameter_template: templateOf(values),
          allowed_ref_patterns: patterns,
          approval_policy_id: values.policyId === '' ? null : values.policyId,
          rollback_supported: values.rollback,
          rollback_pipeline_reference: rollback,
        }),
      );
      return;
    }
    await finish(
      await updateTarget(
        editing.id,
        {
          pipeline_reference: values.pipeline.trim(),
          parameter_template: templateOf(values),
          allowed_ref_patterns: patterns,
          approval_policy_id: values.policyId === '' ? null : values.policyId,
          rollback_supported: values.rollback,
          rollback_pipeline_reference: rollback,
        },
        editing.updated_at,
      ),
    );
  };

  const edit = async (target: TargetSummary): Promise<void> => {
    setBusy(true);
    setNotice(null);
    setProblems([]);
    const result = await readTarget(target.id);
    setBusy(false);
    if (result.kind === 'ok') setForm({ editing: result.value });
    else await finish(result);
  };

  const toggle = async (target: TargetSummary): Promise<void> => {
    setBusy(true);
    setNotice(null);
    await finish(
      await updateTarget(
        target.id,
        { status: target.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' },
        target.updated_at,
      ),
    );
  };

  const retire = async (target: TargetSummary): Promise<void> => {
    setBusy(true);
    setNotice(null);
    await finish(await retireTarget(target.id));
  };

  const data = loaded?.kind === 'ok' ? loaded.value : null;

  return (
    <section aria-labelledby="targets-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 id="targets-heading" className="text-xl font-semibold">
          Deployment targets
        </h1>
        {mayManage && form === null && (
          <button
            type="button"
            disabled={readOnly || data === null}
            onClick={() => {
              setProblems([]);
              setNotice(null);
              setForm({ editing: null });
            }}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Register target
          </button>
        )}
      </header>

      <p
        role="note"
        className="mb-4 rounded border px-3 py-2 text-sm"
        style={{ borderColor: 'var(--c-border)', ...muted }}
      >
        These are the only pipelines DCP may run, one per application and environment. Registering a
        target does not deploy anything.
      </p>

      {data?.jenkins.status === 'not_configured' && (
        <p
          role="status"
          className="mb-4 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
        >
          <strong>Jenkins is not configured on this deployment.</strong> Registered pipelines cannot
          be run until an administrator sets it up.
        </p>
      )}
      {data?.jenkins.status === 'configured' && data.jenkins.demo_mode && (
        <p
          role="status"
          className="mb-4 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
        >
          Demonstration mode: Jenkins here is a mock, not a real build system.
        </p>
      )}

      {notice !== null && (
        <div
          role="alert"
          className="mb-4 rounded border px-4 py-3 text-sm"
          style={{ borderColor: 'var(--c-critical)', color: 'var(--c-critical)' }}
        >
          {notice}
        </div>
      )}

      {form !== null && (
        <TargetForm
          key={form.editing?.id ?? 'new'}
          initial={form.editing === null ? blank : valuesOf(form.editing)}
          editing={form.editing !== null}
          applications={applications}
          policies={policies}
          busy={busy}
          problems={problems}
          onSubmit={(values) => {
            void submit(values);
          }}
          onCancel={() => {
            setForm(null);
            setProblems([]);
            setNotice(null);
          }}
        />
      )}

      {loading && data === null && <LoadingState what="deployment targets" />}
      {loaded !== null && loaded.kind !== 'ok' && (
        <ErrorState result={loaded} onRetry={() => void reload()} />
      )}

      {data !== null && data.items.length === 0 && (
        <EmptyState message="No deployment target is registered, or none that you may see. A target outside your scope is not listed." />
      )}

      {data !== null && data.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Registered deployment targets</caption>
          <thead>
            <tr style={muted}>
              <th className="py-1 pr-3">Application</th>
              <th className="py-1 pr-3">Environment</th>
              <th className="py-1 pr-3">Pipeline</th>
              <th className="py-1 pr-3">Allowed refs</th>
              <th className="py-1 pr-3">Approval</th>
              <th className="py-1 pr-3">Rollback</th>
              <th className="py-1 pr-3">Status</th>
              {mayManage && (
                <th className="py-1">
                  <span className="sr-only">Actions</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {data.items.map((target) => (
              <tr key={target.id} className="border-t" style={{ borderColor: 'var(--c-border)' }}>
                <td className="py-2 pr-3">
                  <Link to={`/applications/${target.application_id}`} className="underline">
                    {target.application_name}
                  </Link>
                </td>
                <td className="py-2 pr-3">{target.environment}</td>
                <td className="py-2 pr-3">
                  <code>{target.pipeline_reference}</code>
                </td>
                <td className="py-2 pr-3">{refSummary(target)}</td>
                <td className="py-2 pr-3">
                  {target.requires_approval ? 'Required' : 'Not required'}
                </td>
                <td className="py-2 pr-3">
                  {target.rollback_supported ? 'Supported' : 'Not supported'}
                </td>
                <td className="py-2 pr-3">
                  <StatusBadge value={target.status} />
                </td>
                {mayManage && (
                  <td className="py-2">
                    {retiring === target.id ? (
                      <span role="group" aria-label={`Retire ${target.application_name}?`}>
                        Retire this target?{' '}
                        <button
                          type="button"
                          disabled={busy || readOnly}
                          onClick={() => {
                            void retire(target);
                          }}
                          className="mr-2 rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                          style={{ borderColor: 'var(--c-critical)' }}
                        >
                          Confirm retire
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setRetiring(null);
                          }}
                          className="rounded border px-2 py-0.5 text-xs"
                          style={{ borderColor: 'var(--c-border)' }}
                        >
                          Keep
                        </button>
                      </span>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy || readOnly}
                          onClick={() => {
                            void edit(target);
                          }}
                          className="mr-2 rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                          style={{ borderColor: 'var(--c-border)' }}
                          aria-label={`Edit ${target.application_name} ${target.environment}`}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          disabled={busy || readOnly}
                          onClick={() => {
                            void toggle(target);
                          }}
                          className="mr-2 rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                          style={{ borderColor: 'var(--c-border)' }}
                          aria-label={`${target.status === 'ACTIVE' ? 'Disable' : 'Enable'} ${target.application_name} ${target.environment}`}
                        >
                          {target.status === 'ACTIVE' ? 'Disable' : 'Enable'}
                        </button>
                        <button
                          type="button"
                          disabled={busy || readOnly}
                          onClick={() => {
                            setRetiring(target.id);
                          }}
                          className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                          style={{ borderColor: 'var(--c-border)' }}
                          aria-label={`Retire ${target.application_name} ${target.environment}`}
                        >
                          Retire
                        </button>
                      </>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data !== null && (
        <Pager
          paging={paging}
          nextCursor={data.page.next_cursor}
          hasMore={data.page.has_more}
          label="Deployment targets"
        />
      )}
    </section>
  );
}
