/**
 * Server registration form (§14.2).
 *
 * The part worth reading is the error handling. A `422` from this API carries
 * a per-field list (§8) precisely so a form does not have to parse prose, and
 * this maps `details.fields[].loc` straight onto the inputs. Anything that
 * does not match a field it renders — a header problem, a cross-field rule —
 * still gets shown at the top rather than swallowed; a validation error the
 * user cannot see is a form that appears to do nothing when submitted.
 *
 * `Idempotency-Key` is generated per submission by the client
 * (`api/inventory.ts`), so a double-click creates one server and the second
 * request returns the first one's row (§7).
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { registerServer, type FieldProblem } from '@/api/inventory';
import { Failure } from '@/pages/inventory/states';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const OPERATING_SYSTEMS = ['LINUX', 'WINDOWS', 'OTHER'];
const MANAGEMENT_METHODS = [
  'AWS_SSM',
  'AZURE_RUN_COMMAND',
  'AZURE_ARC',
  'RESTRICTED_AGENT',
  'NONE',
];

const FIELDS = [
  'cloud_account_id',
  'provider_resource_id',
  'hostname',
  'display_name',
  'os_version',
  'region',
  'private_ip',
  'owner_team',
  'metrics_instance',
] as const;

type TextField = (typeof FIELDS)[number];

export function ServerForm(): React.JSX.Element {
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, string>>({
    cloud_account_id: '',
    provider_resource_id: '',
    hostname: '',
    display_name: '',
    operating_system: 'LINUX',
    environment: 'staging',
    management_method: 'RESTRICTED_AGENT',
    os_version: '',
    region: '',
    private_ip: '',
    owner_team: '',
    metrics_instance: '',
  });
  const [problems, setProblems] = useState<FieldProblem[]>([]);
  const [failure, setFailure] = useState<Parameters<typeof Failure>[0]['result'] | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (name: string) => (event: { target: { value: string } }) => {
    setValues((current) => ({ ...current, [name]: event.target.value }));
  };

  const problemFor = (name: string): FieldProblem | undefined =>
    problems.find((problem) => problem.field === name);

  const submit = async (event: React.SyntheticEvent) => {
    event.preventDefault();
    setProblems([]);
    setFailure(null);
    setSubmitting(true);
    // Empty optional fields are omitted rather than sent as "", which the
    // backend would reject on min_length — an empty box means "not supplied".
    const body = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ''));
    const result = await registerServer({ ...body, monitoring_enabled: true });
    setSubmitting(false);
    if (result.kind === 'ok') {
      void navigate(`/servers/${result.value.id}`);
      return;
    }
    if (result.kind === 'invalid') {
      setProblems(result.problems);
      // Problems that match no rendered field would otherwise vanish.
      const named = new Set<string>([
        ...FIELDS,
        'operating_system',
        'environment',
        'management_method',
      ]);
      if (!result.problems.some((problem) => named.has(problem.field))) setFailure(result);
      return;
    }
    setFailure(result);
  };

  const field = (name: TextField, label: string, required = false) => {
    const problem = problemFor(name);
    return (
      <label className="block text-sm">
        <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
          {label}
          {required && ' *'}
        </span>
        <input
          name={name}
          value={values[name]}
          onChange={set(name)}
          aria-invalid={problem !== undefined}
          aria-describedby={problem !== undefined ? `${name}-error` : undefined}
          className="w-full rounded border px-2 py-1"
          style={{
            borderColor: problem !== undefined ? 'var(--c-critical)' : 'var(--c-border)',
            background: 'var(--c-surface)',
          }}
        />
        {problem !== undefined && (
          <span
            id={`${name}-error`}
            role="alert"
            className="mt-1 block text-xs"
            style={{ color: 'var(--c-critical)' }}
          >
            {problem.message}
          </span>
        )}
      </label>
    );
  };

  const choice = (name: string, label: string, options: string[]) => {
    const problem = problemFor(name);
    return (
      <label className="block text-sm">
        <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
          {label} *
        </span>
        <select
          name={name}
          value={values[name]}
          onChange={set(name)}
          aria-invalid={problem !== undefined}
          className="w-full rounded border px-2 py-1"
          style={{
            borderColor: problem !== undefined ? 'var(--c-critical)' : 'var(--c-border)',
            background: 'var(--c-surface)',
          }}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        {problem !== undefined && (
          <span role="alert" className="mt-1 block text-xs" style={{ color: 'var(--c-critical)' }}>
            {problem.message}
          </span>
        )}
      </label>
    );
  };

  return (
    <section aria-labelledby="register-heading">
      <h1 id="register-heading" className="mb-1 text-xl font-semibold">
        Register a server
      </h1>
      <p className="mb-4 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        A newly registered server is <strong>PENDING</strong> and grants no operational permission.
      </p>

      {failure !== null && <Failure result={failure} />}

      <form
        onSubmit={(event) => void submit(event)}
        className="grid max-w-2xl gap-4 sm:grid-cols-2"
      >
        {field('cloud_account_id', 'Cloud account id', true)}
        {field('provider_resource_id', 'Provider resource id', true)}
        {field('hostname', 'Hostname', true)}
        {field('display_name', 'Display name', true)}
        {choice('operating_system', 'Operating system', OPERATING_SYSTEMS)}
        {choice('environment', 'Environment', ENVIRONMENTS)}
        {choice('management_method', 'Management method', MANAGEMENT_METHODS)}
        {field('os_version', 'OS version')}
        {field('region', 'Region')}
        {field('private_ip', 'Private IP')}
        {field('owner_team', 'Owner team')}
        {field('metrics_instance', 'Metrics instance (host:port)')}

        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={submitting}
            className="rounded border px-3 py-1 text-sm"
            style={{ borderColor: 'var(--c-border)', opacity: submitting ? 0.5 : 1 }}
          >
            {submitting ? 'Registering…' : 'Register'}
          </button>
        </div>
      </form>
    </section>
  );
}
