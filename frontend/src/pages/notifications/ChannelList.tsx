/**
 * Notification channels (§26.8 - §26.10): where alerts are sent, and whether sending works.
 *
 * What this page owes the administrator:
 *
 * 1. **A destination is a reference, not a secret.** The form asks for `env://NAME` (or an
 *    address, for email) and refuses a pasted URL before it is sent; the backend refuses it
 *    too and never echoes it. Nothing on this page can display a resolved destination,
 *    because the API never returns one.
 * 2. **A channel that has never delivered is not "working".** Health comes from the
 *    deliveries (`sent`, `failed` in the last day, the newest failure's reason), and a channel
 *    with no history says so.
 * 3. **Rules say what they match.** An empty rule set sends everything, and the page says so
 *    instead of leaving "no filters" to be read as "no alerts".
 * 4. **A channel hears that a problem began, not that it ended** (`OPEN-51`); the page says it
 *    once, because a quiet channel after a recovery is otherwise a surprise.
 *
 * Permission and read-only mode only decide what is *shown*; the backend answers `403` and
 * `423` on its own account, and each is shown as what it is.
 */

import { useState } from 'react';

import {
  createChannel,
  describeRules,
  listChannels,
  reasonText,
  updateChannel,
  type Channel,
  type ChannelInput,
  type ChannelList as Channels,
  type ChannelType,
  type RoutingRules,
} from '@/api/notifications';
import type { FieldProblem, Result } from '@/api/inventory';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { StatusBadge } from '@/components/StatusBadge';
import { useLatestResponse } from '@/components/useLatestResponse';

const TYPES: readonly ChannelType[] = ['WEBHOOK', 'SLACK', 'TEAMS', 'EMAIL'];
const SEVERITIES = ['CRITICAL', 'WARNING', 'INFO'] as const;
const ENVIRONMENTS = ['production', 'staging', 'development', 'other'] as const;
const TARGETS = ['server', 'service'] as const;

function toggled<T extends string>(list: readonly T[] | undefined, value: T): T[] {
  const current = list ?? [];
  return current.includes(value) ? current.filter((item) => item !== value) : [...current, value];
}

function looksLikeASecretValue(reference: string): boolean {
  return reference.includes('://') && !reference.startsWith('env://');
}

interface FormValues {
  name: string;
  channelType: ChannelType;
  reference: string;
  rules: RoutingRules;
}

function CheckGroup<T extends string>({
  legend,
  options,
  selected,
  onToggle,
}: {
  legend: string;
  options: readonly T[];
  selected: readonly T[] | undefined;
  onToggle: (value: T) => void;
}): React.JSX.Element {
  return (
    <fieldset className="text-sm">
      <legend className="mb-1" style={{ color: 'var(--c-text-muted)' }}>
        {legend}
      </legend>
      <div className="flex flex-wrap gap-3">
        {options.map((option) => (
          <label key={option} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={selected?.includes(option) ?? false}
              onChange={() => {
                onToggle(option);
              }}
            />
            {option}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function ChannelForm({
  initial,
  editing,
  emailAvailable,
  busy,
  problems,
  onSubmit,
  onCancel,
}: {
  initial: FormValues;
  editing: boolean;
  emailAvailable: boolean;
  busy: boolean;
  problems: FieldProblem[];
  onSubmit: (values: FormValues) => void;
  onCancel: () => void;
}): React.JSX.Element {
  const [values, setValues] = useState<FormValues>(initial);
  const [clientProblem, setClientProblem] = useState<string | null>(null);
  const isEmail = values.channelType === 'EMAIL';
  const field = (name: string): string | undefined =>
    problems.find((problem) => problem.field === name)?.message;
  const input = 'rounded border px-2 py-1 w-full';
  const inputStyle = { borderColor: 'var(--c-border)', background: 'var(--c-surface)' };

  const submit = (event: React.SyntheticEvent): void => {
    event.preventDefault();
    if (looksLikeASecretValue(values.reference)) {
      // A webhook URL is the credential. It belongs in the secret store, not in this form.
      setClientProblem(
        'That looks like a URL. A webhook URL is a secret: store it in the secret store and enter a reference such as env://NAME here.',
      );
      return;
    }
    setClientProblem(null);
    onSubmit(values);
  };

  return (
    <form
      onSubmit={submit}
      aria-label={editing ? 'Edit channel' : 'New channel'}
      className="mb-6 space-y-3 rounded-lg border px-4 py-3 text-sm"
      style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
    >
      <label className="block">
        <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
          Name
        </span>
        <input
          required
          maxLength={100}
          value={values.name}
          onChange={(event) => {
            setValues({ ...values, name: event.target.value });
          }}
          className={input}
          style={inputStyle}
        />
        {field('name') !== undefined && <span role="alert">{field('name')}</span>}
      </label>

      <label className="block">
        <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
          Type
        </span>
        <select
          value={values.channelType}
          disabled={editing}
          onChange={(event) => {
            setValues({ ...values, channelType: event.target.value as ChannelType });
          }}
          className={input}
          style={inputStyle}
        >
          {TYPES.map((type) => (
            <option key={type} value={type} disabled={type === 'EMAIL' && !emailAvailable}>
              {type}
            </option>
          ))}
        </select>
        {!emailAvailable && (
          <span className="mt-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Email is not configured on this deployment (no SMTP host), so it cannot be chosen.
          </span>
        )}
        {field('channel_type') !== undefined && <span role="alert">{field('channel_type')}</span>}
      </label>

      <label className="block">
        <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
          {isEmail ? 'Recipient address' : 'Secret reference (for example env://OPS_SLACK)'}
        </span>
        <input
          required
          maxLength={512}
          value={values.reference}
          onChange={(event) => {
            setValues({ ...values, reference: event.target.value });
          }}
          className={input}
          style={inputStyle}
          autoComplete="off"
          spellCheck={false}
        />
        {!isEmail && (
          <span className="mt-1 block" style={{ color: 'var(--c-text-muted)' }}>
            The webhook URL itself is never entered here, and is never shown by this page.
          </span>
        )}
        {clientProblem !== null && <span role="alert">{clientProblem}</span>}
        {field('destination_reference') !== undefined && (
          <span role="alert">{field('destination_reference')}</span>
        )}
      </label>

      <div className="space-y-2">
        <p style={{ color: 'var(--c-text-muted)' }}>
          Send alerts matching all of the following. Leave a group empty to match everything in it;
          leave all empty to send every alert.
        </p>
        <CheckGroup
          legend="Severity"
          options={SEVERITIES}
          selected={values.rules.severity}
          onToggle={(value) => {
            setValues({
              ...values,
              rules: { ...values.rules, severity: toggled(values.rules.severity, value) },
            });
          }}
        />
        <CheckGroup
          legend="Environment"
          options={ENVIRONMENTS}
          selected={values.rules.environment}
          onToggle={(value) => {
            setValues({
              ...values,
              rules: { ...values.rules, environment: toggled(values.rules.environment, value) },
            });
          }}
        />
        <CheckGroup
          legend="About"
          options={TARGETS}
          selected={values.rules.target_type}
          onToggle={(value) => {
            setValues({
              ...values,
              rules: { ...values.rules, target_type: toggled(values.rules.target_type, value) },
            });
          }}
        />
        <p style={{ color: 'var(--c-text-muted)' }}>
          An alert that is about no known server has no environment, so a channel restricted to an
          environment does not receive it.
        </p>
      </div>

      <div className="flex gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded border px-3 py-1 disabled:opacity-50"
          style={{ borderColor: 'var(--c-border)' }}
        >
          {editing ? 'Save changes' : 'Create channel'}
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

function Health({ channel }: { channel: Channel }): React.JSX.Element {
  const d = channel.deliveries;
  const never = d.last_success_at == null && d.last_failure_at == null && d.pending === 0;
  if (never) {
    return <span style={{ color: 'var(--c-text-muted)' }}>Nothing sent yet — not proven.</span>;
  }
  return (
    <span>
      {d.sent_24h} sent, {d.failed_24h} failed (24 h)
      {d.pending > 0 && <>, {d.pending} waiting</>}
      {d.last_error != null && d.last_error !== '' && (
        <span style={{ color: 'var(--c-critical)' }}>
          {' '}
          · last failure: {reasonText(d.last_error)}
        </span>
      )}
    </span>
  );
}

export function ChannelList(): React.JSX.Element {
  const mayManage = useCanRender(PERMISSIONS.adminIntegrations);
  const readOnly = useIsReadOnly();
  const { value: loaded, loading, reload } = useLatestResponse(() => listChannels(), []);
  const [form, setForm] = useState<{ editing: Channel | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<FieldProblem[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  if (!mayManage) {
    return (
      <p role="status" style={{ color: 'var(--c-text-muted)' }}>
        Notification channels are managed by an administrator.
      </p>
    );
  }

  const finish = async <T,>(result: Result<T>): Promise<void> => {
    setBusy(false);
    if (result.kind === 'ok') {
      setForm(null);
      setProblems([]);
      setNotice(null);
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
        ? 'A channel with that name already exists.'
        : result.kind === 'forbidden'
          ? 'You do not have permission to do that.'
          : result.kind === 'read_only'
            ? 'The portal is in read-only mode. State-changing actions are disabled.'
            : result.kind === 'not_found'
              ? 'That channel no longer exists.'
              : `Could not reach the backend: ${result.reason}`,
    );
  };

  const submit = async (values: FormValues): Promise<void> => {
    setBusy(true);
    setProblems([]);
    setNotice(null);
    const editing = form?.editing ?? null;
    if (editing === null) {
      const input: ChannelInput = {
        name: values.name.trim(),
        channel_type: values.channelType,
        destination_reference: values.reference.trim(),
        routing_rules: values.rules,
      };
      await finish(await createChannel(input));
    } else {
      await finish(
        await updateChannel(editing.id, {
          name: values.name.trim(),
          destination_reference: values.reference.trim(),
          routing_rules: values.rules,
        }),
      );
    }
  };

  const toggle = async (channel: Channel): Promise<void> => {
    setBusy(true);
    setNotice(null);
    await finish(
      await updateChannel(channel.id, {
        status: channel.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE',
      }),
    );
  };

  const data: Channels | null = loaded?.kind === 'ok' ? loaded.value : null;
  const emailAvailable = data?.email_available ?? false;
  const blank: FormValues = {
    name: '',
    channelType: 'WEBHOOK',
    reference: '',
    rules: {},
  };

  return (
    <section aria-labelledby="channels-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 id="channels-heading" className="text-xl font-semibold">
          Notification channels
        </h1>
        {form === null && (
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
            New channel
          </button>
        )}
      </header>

      <p
        role="note"
        className="mb-4 rounded border px-3 py-2 text-sm"
        style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
      >
        A channel is told when an alert <strong>begins</strong>. It is not told about repeats,
        acknowledgements or recoveries, so a quiet channel after a recovery is expected.
      </p>

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
        <ChannelForm
          key={form.editing?.id ?? 'new'}
          initial={
            form.editing === null
              ? blank
              : {
                  name: form.editing.name,
                  channelType: form.editing.channel_type,
                  reference: form.editing.destination_reference,
                  rules: form.editing.routing_rules_valid ? form.editing.routing_rules : {},
                }
          }
          editing={form.editing !== null}
          emailAvailable={emailAvailable}
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

      {loading && data === null && <LoadingState what="notification channels" />}
      {loaded !== null && loaded.kind !== 'ok' && (
        <ErrorState result={loaded} onRetry={() => void reload()} />
      )}

      {data !== null && data.items.length === 0 && (
        <EmptyState message="No channel is registered. Alerts are shown here and sent nowhere; register a channel to be told when one begins." />
      )}

      {data !== null && data.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Notification channels</caption>
          <thead>
            <tr style={{ color: 'var(--c-text-muted)' }}>
              <th className="py-1 pr-3">Name</th>
              <th className="py-1 pr-3">Type</th>
              <th className="py-1 pr-3">Destination</th>
              <th className="py-1 pr-3">Sends</th>
              <th className="py-1 pr-3">Status</th>
              <th className="py-1 pr-3">Deliveries</th>
              <th className="py-1">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((channel) => (
              <tr key={channel.id} className="border-t" style={{ borderColor: 'var(--c-border)' }}>
                <td className="py-2 pr-3">{channel.name}</td>
                <td className="py-2 pr-3">{channel.channel_type}</td>
                <td className="py-2 pr-3">
                  <code>{channel.destination_reference}</code>
                </td>
                <td className="py-2 pr-3">
                  {channel.routing_rules_valid ? (
                    describeRules(channel.routing_rules)
                  ) : (
                    <span role="alert" style={{ color: 'var(--c-critical)' }}>
                      Rules unreadable: this channel sends nothing until they are saved again.
                    </span>
                  )}
                </td>
                <td className="py-2 pr-3">
                  <StatusBadge value={channel.status} />
                </td>
                <td className="py-2 pr-3">
                  <Health channel={channel} />
                </td>
                <td className="py-2">
                  <button
                    type="button"
                    disabled={busy || readOnly}
                    onClick={() => {
                      setProblems([]);
                      setNotice(null);
                      setForm({ editing: channel });
                    }}
                    className="mr-2 rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                    style={{ borderColor: 'var(--c-border)' }}
                    aria-label={`Edit ${channel.name}`}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    disabled={busy || readOnly}
                    onClick={() => {
                      void toggle(channel);
                    }}
                    className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                    style={{ borderColor: 'var(--c-border)' }}
                    aria-label={`${channel.status === 'ACTIVE' ? 'Disable' : 'Enable'} ${channel.name}`}
                  >
                    {channel.status === 'ACTIVE' ? 'Disable' : 'Enable'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
