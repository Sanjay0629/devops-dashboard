/**
 * Server registration (§14.2) and server edit (§14.4).
 *
 * Both live here and share the two input renderers below, because the part
 * worth getting right is the same in both. A `422` from this API carries a
 * per-field list (§8) precisely so a form does not have to parse prose, and
 * `details.fields[].loc` maps straight onto the inputs. Anything that matches
 * no rendered field — a header problem, a cross-field rule — is still shown
 * at the top rather than swallowed; a validation error the user cannot see is
 * a form that appears to do nothing when submitted.
 *
 * Two components rather than one with a mode flag: edit offers a different
 * set of fields (§14.4's mutable nine, and **no immutable field at all** —
 * `id`, `cloud_account_id`, `provider_resource_id` and `created_at` answer
 * `422 not_permitted`, so they are not rendered as inputs to be rejected),
 * loads before it renders, and carries two things registration has no
 * equivalent of: the environment-change warning and the `If-Unmodified-Since`
 * conflict.
 *
 * `Idempotency-Key` is generated per submission by the client
 * (`api/inventory.ts`), so a double-click creates one server and the second
 * request returns the first one's row (§7). `PATCH` is idempotent by nature
 * and §14.4 defines no key for it, so the edit form sends none.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  readServer,
  registerServer,
  updateServer,
  type FieldProblem,
  type Result,
  type ServerDetail as ServerDetailShape,
  type ServerUpdate,
} from '@/api/inventory';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState, type Failed } from '@/components/ErrorState';

/**
 * §14.4's two closed sets, typed from the generated contract rather than
 * written out again: a backend enum change breaks the build here instead of
 * leaving an option the API will reject.
 */
type EnvironmentName = Exclude<ServerUpdate['environment'], null | undefined>;
type SettableStatus = Exclude<ServerUpdate['status'], null | undefined>;

const ENVIRONMENTS: readonly EnvironmentName[] = ['development', 'staging', 'production', 'other'];
const OPERATING_SYSTEMS = ['LINUX', 'WINDOWS', 'OTHER'];
const MANAGEMENT_METHODS = [
  'AWS_SSM',
  'AZURE_RUN_COMMAND',
  'AZURE_ARC',
  'RESTRICTED_AGENT',
  'NONE',
];
/** `PENDING`↔`MANAGED`↔`RETIRED` only, and nothing else is offered. */
const SETTABLE_STATUSES: readonly SettableStatus[] = ['PENDING', 'MANAGED', 'RETIRED'];

const REGISTER_FIELDS = [
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

/** The editable text fields of §14.4, in the order they are rendered. */
type EditableField =
  'display_name' | 'owner_team' | 'os_version' | 'region' | 'private_ip' | 'metrics_instance';

const EDIT_FIELDS: readonly (readonly [EditableField, string])[] = [
  ['display_name', 'Display name'],
  ['owner_team', 'Owner team'],
  ['os_version', 'OS version'],
  ['region', 'Region'],
  ['private_ip', 'Private IP'],
  ['metrics_instance', 'Metrics instance (host:port)'],
];

/**
 * Where an emptied box means `null` — "unset this" — rather than "leave it".
 *
 * The backend dumps the body with `exclude_unset=True`, so absent and `null`
 * are two different instructions and omitting a cleared field would make
 * clearing impossible. `display_name` is **not** in the set: its column is
 * `NOT NULL`, so the input is `required` instead of clearable.
 */
const CLEARABLE: ReadonlySet<EditableField> = new Set<EditableField>([
  'owner_team',
  'os_version',
  'region',
  'private_ip',
  'metrics_instance',
]);

const problemFor = (problems: FieldProblem[], name: string): FieldProblem | undefined =>
  problems.find((problem) => problem.field === name);

const fieldStyle = (problem: FieldProblem | undefined) => ({
  borderColor: problem !== undefined ? 'var(--c-critical)' : 'var(--c-border)',
  background: 'var(--c-surface)',
});

function Problem({ name, problem }: { name: string; problem: FieldProblem | undefined }) {
  if (problem === undefined) return null;
  return (
    <span
      id={`${name}-error`}
      role="alert"
      className="mt-1 block text-xs"
      style={{ color: 'var(--c-critical)' }}
    >
      {problem.message}
    </span>
  );
}

function TextField({
  name,
  label,
  value,
  problem,
  required = false,
  onChange,
}: {
  name: string;
  label: string;
  value: string;
  problem: FieldProblem | undefined;
  required?: boolean;
  onChange: (value: string) => void;
}): React.JSX.Element {
  return (
    <label className="block text-sm">
      <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
        {label}
        {required && ' *'}
      </span>
      <input
        name={name}
        value={value}
        required={required}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        aria-invalid={problem !== undefined}
        aria-describedby={problem !== undefined ? `${name}-error` : undefined}
        className="w-full rounded border px-2 py-1"
        style={fieldStyle(problem)}
      />
      <Problem name={name} problem={problem} />
    </label>
  );
}

function ChoiceField({
  name,
  label,
  value,
  options,
  problem,
  onChange,
}: {
  name: string;
  label: string;
  value: string;
  options: readonly string[];
  problem: FieldProblem | undefined;
  onChange: (value: string) => void;
}): React.JSX.Element {
  return (
    <label className="block text-sm">
      <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
        {label} *
      </span>
      <select
        name={name}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        aria-invalid={problem !== undefined}
        aria-describedby={problem !== undefined ? `${name}-error` : undefined}
        className="w-full rounded border px-2 py-1"
        style={fieldStyle(problem)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
      <Problem name={name} problem={problem} />
    </label>
  );
}

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
  const [failure, setFailure] = useState<Failed<unknown> | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (name: string) => (value: string) => {
    setValues((current) => ({ ...current, [name]: value }));
  };

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
        ...REGISTER_FIELDS,
        'operating_system',
        'environment',
        'management_method',
      ]);
      if (!result.problems.some((problem) => named.has(problem.field))) setFailure(result);
      return;
    }
    setFailure(result);
  };

  const text = (name: (typeof REGISTER_FIELDS)[number], label: string, required = false) => (
    <TextField
      name={name}
      label={label}
      value={values[name] ?? ''}
      required={required}
      problem={problemFor(problems, name)}
      onChange={set(name)}
    />
  );

  const choice = (name: string, label: string, options: readonly string[]) => (
    <ChoiceField
      name={name}
      label={label}
      value={values[name] ?? ''}
      options={options}
      problem={problemFor(problems, name)}
      onChange={set(name)}
    />
  );

  return (
    <section aria-labelledby="register-heading">
      <h1 id="register-heading" className="mb-1 text-xl font-semibold">
        Register a server
      </h1>
      <p className="mb-4 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        A newly registered server is <strong>PENDING</strong> and grants no operational permission.
      </p>

      {failure !== null && <ErrorState result={failure} />}

      <form
        onSubmit={(event) => void submit(event)}
        className="grid max-w-2xl gap-4 sm:grid-cols-2"
      >
        {text('cloud_account_id', 'Cloud account id', true)}
        {text('provider_resource_id', 'Provider resource id', true)}
        {text('hostname', 'Hostname', true)}
        {text('display_name', 'Display name', true)}
        {choice('operating_system', 'Operating system', OPERATING_SYSTEMS)}
        {choice('environment', 'Environment', ENVIRONMENTS)}
        {choice('management_method', 'Management method', MANAGEMENT_METHODS)}
        {text('os_version', 'OS version')}
        {text('region', 'Region')}
        {text('private_ip', 'Private IP')}
        {text('owner_team', 'Owner team')}
        {text('metrics_instance', 'Metrics instance (host:port)')}

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

/** The §14.4 body for the fields that actually moved, and nothing else. */
function changedFields(
  server: ServerDetailShape,
  values: Record<string, string>,
  monitoring: boolean,
): ServerUpdate {
  const body: ServerUpdate = {};
  for (const [name] of EDIT_FIELDS) {
    const typed = values[name] ?? '';
    const next = typed === '' ? (CLEARABLE.has(name) ? null : undefined) : typed;
    if (next !== undefined && next !== (server[name] ?? null)) body[name] = next;
  }
  // The two selects offer §14.4's closed sets and nothing else, so these
  // assert what the markup already restricts — and a value outside them
  // would be a `422` from the enum rather than a silent write.
  if (values.environment !== server.environment) {
    body.environment = values.environment as EnvironmentName;
  }
  if (values.status !== server.status) body.status = values.status as SettableStatus;
  if (monitoring !== server.monitoring_enabled) body.monitoring_enabled = monitoring;
  return body;
}

export function ServerEditForm(): React.JSX.Element {
  const { serverId = '' } = useParams();
  const navigate = useNavigate();
  const [loaded, setLoaded] = useState<Result<ServerDetailShape> | null>(null);
  const [server, setServer] = useState<ServerDetailShape | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [monitoring, setMonitoring] = useState(true);
  const [confirmedEnvironment, setConfirmedEnvironment] = useState(false);
  const [problems, setProblems] = useState<FieldProblem[]>([]);
  const [failure, setFailure] = useState<Failed<unknown> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [unchanged, setUnchanged] = useState(false);
  // Advisory, for usability only: the backend decides every request on its own
  // account (docs/SECURITY.md §6), and an environment change it refuses comes
  // back as `404`, not as a hidden button.
  const mayAdminister = useCanRender(PERMISSIONS.adminUsers);
  const readOnly = useIsReadOnly();

  const load = useCallback(async () => {
    setProblems([]);
    setFailure(null);
    setUnchanged(false);
    setConfirmedEnvironment(false);
    const result = await readServer(serverId);
    setLoaded(result);
    if (result.kind !== 'ok') return;
    const row = result.value;
    setServer(row);
    setMonitoring(row.monitoring_enabled);
    setValues({
      display_name: row.display_name,
      owner_team: row.owner_team ?? '',
      os_version: row.os_version ?? '',
      region: row.region ?? '',
      private_ip: row.private_ip ?? '',
      metrics_instance: row.metrics_instance ?? '',
      environment: row.environment,
      status: row.status,
    });
  }, [serverId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loaded === null) return <LoadingState what="this server" />;
  if (loaded.kind !== 'ok') return <ErrorState result={loaded} />;
  if (server === null) return <LoadingState what="this server" />;

  const set = (name: string) => (value: string) => {
    setValues((current) => ({ ...current, [name]: value }));
  };

  const movingEnvironment = values.environment !== server.environment;

  const submit = async (event: React.SyntheticEvent) => {
    event.preventDefault();
    setProblems([]);
    setFailure(null);
    setUnchanged(false);
    const body = changedFields(server, values, monitoring);
    if (Object.keys(body).length === 0) {
      setUnchanged(true);
      return;
    }
    setSubmitting(true);
    // The `updated_at` of the row this form was filled from, so a concurrent
    // edit answers `409` rather than being overwritten (§14.4).
    const result = await updateServer(server.id, body, server.updated_at);
    setSubmitting(false);
    if (result.kind === 'ok') {
      void navigate(`/servers/${server.id}`);
      return;
    }
    if (result.kind === 'invalid') {
      setProblems(result.problems);
      const named = new Set<string>([
        ...EDIT_FIELDS.map(([name]) => name),
        'environment',
        'status',
        'monitoring_enabled',
      ]);
      if (!result.problems.some((problem) => named.has(problem.field))) setFailure(result);
      return;
    }
    setFailure(result);
  };

  const text = ([name, label]: readonly [EditableField, string]) => (
    <TextField
      key={name}
      name={name}
      label={label}
      value={values[name] ?? ''}
      required={name === 'display_name'}
      problem={problemFor(problems, name)}
      onChange={set(name)}
    />
  );

  return (
    <section aria-labelledby="edit-heading">
      <h1 id="edit-heading" className="mb-1 text-xl font-semibold">
        Edit {server.display_name}
      </h1>
      <p className="mb-4 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        {server.hostname} — the hostname, cloud account and provider resource id identify this host
        and cannot be changed here.
      </p>

      {failure !== null && <ErrorState result={failure} />}
      {failure?.kind === 'conflict' && (
        // Visible, and never retried or overwritten: the row moved under this
        // form, so the only honest options are to look at the new one or to
        // walk away.
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          <button
            type="button"
            onClick={() => void load()}
            className="rounded border px-3 py-1"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Discard my changes and reload
          </button>
          <span style={{ color: 'var(--c-text-muted)' }}>
            Someone else edited this server after this form was opened.
          </span>
        </div>
      )}
      {failure?.kind === 'not_found' && movingEnvironment && (
        // §2 answers `404` rather than `403` for anything outside the caller's
        // scope, so this names both possibilities instead of guessing one.
        <p className="mt-2 text-sm" style={{ color: 'var(--c-text-muted)' }}>
          An environment change needs Administrator, and the destination environment must be in your
          access scope.
        </p>
      )}
      {unchanged && (
        <p role="status" className="mt-2 text-sm" style={{ color: 'var(--c-text-muted)' }}>
          Nothing changed, so nothing was sent.
        </p>
      )}

      <form
        onSubmit={(event) => void submit(event)}
        className="mt-4 grid max-w-2xl gap-4 sm:grid-cols-2"
      >
        {EDIT_FIELDS.map(text)}

        <ChoiceField
          name="status"
          label="Status"
          value={values.status ?? ''}
          options={SETTABLE_STATUSES}
          problem={problemFor(problems, 'status')}
          onChange={set('status')}
        />

        <label className="flex items-center gap-2 self-end text-sm">
          <input
            type="checkbox"
            name="monitoring_enabled"
            checked={monitoring}
            onChange={(event) => {
              setMonitoring(event.target.checked);
            }}
          />
          <span>Monitoring enabled</span>
        </label>

        <fieldset
          className="rounded border p-3 sm:col-span-2"
          style={{ borderColor: movingEnvironment ? 'var(--c-warning)' : 'var(--c-border)' }}
        >
          <legend className="px-1 text-sm font-medium">Environment</legend>
          <ChoiceField
            name="environment"
            label="Environment"
            value={values.environment ?? ''}
            options={ENVIRONMENTS}
            problem={problemFor(problems, 'environment')}
            onChange={set('environment')}
          />
          <p className="mt-2 text-xs" style={{ color: 'var(--c-text-muted)' }}>
            Not an ordinary field edit. The environment is the authorisation boundary for every
            future operation on this host, so changing it requires <strong>Administrator</strong>{' '}
            and is audited separately as <code>SERVER_ENVIRONMENT_CHANGED</code>.
          </p>
          {!mayAdminister && (
            <p className="mt-2 text-xs" style={{ color: 'var(--c-warning)' }}>
              Your session does not list Administrator, so the backend will refuse an environment
              change. Every other field on this form is yours to edit.
            </p>
          )}
          {movingEnvironment && (
            <label className="mt-2 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={confirmedEnvironment}
                onChange={(event) => {
                  setConfirmedEnvironment(event.target.checked);
                }}
              />
              <span>
                Move this server from <strong>{server.environment}</strong> to{' '}
                <strong>{values.environment}</strong>.
              </span>
            </label>
          )}
        </fieldset>

        <div className="flex gap-3 sm:col-span-2">
          <button
            type="submit"
            disabled={submitting || readOnly || (movingEnvironment && !confirmedEnvironment)}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            style={{ borderColor: 'var(--c-border)' }}
          >
            {submitting ? 'Saving…' : 'Save changes'}
          </button>
          <button
            type="button"
            onClick={() => void navigate(`/servers/${server.id}`)}
            className="rounded border px-3 py-1 text-sm"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}
