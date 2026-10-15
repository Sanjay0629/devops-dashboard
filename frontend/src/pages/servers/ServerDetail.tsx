/**
 * Server detail, Overview tab (§14.3).
 *
 * The honest-states rule does most of the work here. Health, open alerts and
 * provider capabilities have no source until Phase 2–4, and each says which
 * kind of "nothing" it is rather than rendering a zero or a dash — PRD §4.2
 * and §4.4 treat unknown, unavailable, unsupported and not-configured as four
 * different facts, and CLAUDE.md §5 forbids substituting one for another.
 *
 * Only an Overview tab: metrics, services, logs and operations are the later
 * milestones' tabs, and an empty tab labelled "Metrics" would imply this host
 * has none rather than that nothing collects them yet.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import {
  associateApplication,
  listApplications,
  readServer,
  removeAssociation,
  retireServer,
  type ApplicationSummary,
  type Page,
  type Result,
  type ServerDetail as ServerDetailShape,
} from '@/api/inventory';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { DataStateBanner } from '@/components/DataStateBanner';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState, type Failed } from '@/components/ErrorState';
import { Pager } from '@/components/Pager';
import { StatusBadge } from '@/components/StatusBadge';
import { useCursorPaging } from '@/components/useCursorPaging';

function Row({ label, children }: { label: string; children: React.ReactNode }) {
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

/** A value the operator never supplied, said as such. */
function Optional({ value }: { value: string | null }): React.JSX.Element {
  return value === null ? (
    <span style={{ color: 'var(--c-text-muted)' }}>Not recorded</span>
  ) : (
    <>{value}</>
  );
}

/**
 * Pick a registered application and associate it (§14.6).
 *
 * The candidate list is the paged §18.1 one — search box, one page at a time,
 * the same `Pager` the three list pages use — because an unpaged `<select>`
 * would quietly present the first 50 applications as though they were all of
 * them.
 *
 * Applications already on this server are **not** filtered out of the
 * candidates: §14.6 answers `409` for a duplicate with a message that says
 * exactly that, and a client-side exclusion would be a second, divergent copy
 * of a rule the backend already enforces.
 */
function AssociateApplication({
  serverId,
  onAssociated,
}: {
  serverId: string;
  onAssociated: () => void;
}): React.JSX.Element {
  const [q, setQ] = useState('');
  const [candidates, setCandidates] = useState<Result<Page<ApplicationSummary>> | null>(null);
  const [chosen, setChosen] = useState('');
  const [role, setRole] = useState('');
  const [failure, setFailure] = useState<Failed<unknown> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const paging = useCursorPaging();
  const { cursor, reset } = paging;

  const load = useCallback(async () => {
    setCandidates(null);
    setCandidates(
      await listApplications({ q: q.trim() || undefined, cursor: cursor ?? undefined }),
    );
  }, [q, cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  // A different search is a different collection, so page 1.
  useEffect(() => {
    reset();
  }, [q, reset]);

  const items = candidates?.kind === 'ok' ? candidates.value.items : [];

  const submit = async (event: React.SyntheticEvent) => {
    event.preventDefault();
    setFailure(null);
    if (chosen === '') return;
    setSubmitting(true);
    const result = await associateApplication(serverId, chosen, role.trim());
    setSubmitting(false);
    if (result.kind === 'ok') {
      setChosen('');
      setRole('');
      onAssociated();
      return;
    }
    setFailure(result);
  };

  return (
    <form
      onSubmit={(event) => void submit(event)}
      className="mt-4 rounded border p-3"
      style={{ borderColor: 'var(--c-border)' }}
      aria-labelledby="associate-heading"
    >
      <h3 id="associate-heading" className="mb-2 text-sm font-medium">
        Associate an application
      </h3>

      {failure !== null && <ErrorState result={failure} />}

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Search applications
          </span>
          <input
            type="search"
            value={q}
            placeholder="name or slug"
            onChange={(event) => {
              setQ(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Application
          </span>
          <select
            value={chosen}
            onChange={(event) => {
              setChosen(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">Choose…</option>
            {items.map((application) => (
              <option key={application.id} value={application.id}>
                {application.name} ({application.slug})
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Role on server
          </span>
          <input
            name="role_on_server"
            value={role}
            placeholder="optional"
            onChange={(event) => {
              setRole(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>

        <button
          type="submit"
          disabled={submitting || chosen === ''}
          className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          style={{ borderColor: 'var(--c-border)' }}
        >
          {submitting ? 'Associating…' : 'Associate'}
        </button>
      </div>

      {candidates === null && <LoadingState what="applications" />}
      {candidates !== null && candidates.kind !== 'ok' && <ErrorState result={candidates} />}
      {candidates?.kind === 'ok' && items.length === 0 && (
        <EmptyState message="No applications match that search." />
      )}
      {candidates?.kind === 'ok' && (
        <Pager
          paging={paging}
          nextCursor={candidates.value.page.next_cursor}
          hasMore={candidates.value.page.has_more}
          label="Candidate applications"
        />
      )}
    </form>
  );
}

export function ServerDetail(): React.JSX.Element {
  const { serverId = '' } = useParams();
  const navigate = useNavigate();
  const [result, setResult] = useState<Result<ServerDetailShape> | null>(null);
  const [retireError, setRetireError] = useState<Failed<null> | null>(null);
  const [associationError, setAssociationError] = useState<Failed<null> | null>(null);
  const mayWrite = useCanRender(PERMISSIONS.serverWrite);
  const readOnly = useIsReadOnly();

  const load = useCallback(async () => {
    setResult(await readServer(serverId));
  }, [serverId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (result === null) return <LoadingState what="this server" />;
  if (result.kind !== 'ok') return <ErrorState result={result} />;

  const server = result.value;

  const retire = async () => {
    setRetireError(null);
    const outcome = await retireServer(server.id);
    if (outcome.kind === 'ok') {
      void navigate('/servers');
      return;
    }
    setRetireError(outcome);
  };

  const remove = async (applicationId: string) => {
    setAssociationError(null);
    const outcome = await removeAssociation(server.id, applicationId);
    if (outcome.kind === 'ok') {
      void load();
      return;
    }
    setAssociationError(outcome);
  };

  return (
    <section aria-labelledby="server-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 id="server-heading" className="text-xl font-semibold">
            {server.display_name}
          </h1>
          <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
            {server.hostname}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge value={server.status} />
          {mayWrite && (
            <Link
              to={`/servers/${server.id}/edit`}
              className="rounded border px-3 py-1 text-sm"
              style={{ borderColor: 'var(--c-border)' }}
            >
              Edit
            </Link>
          )}
          {mayWrite && (
            <button
              type="button"
              disabled={readOnly || server.status === 'RETIRED'}
              onClick={() => void retire()}
              className="rounded border px-3 py-1 text-sm"
              style={{
                borderColor: 'var(--c-border)',
                opacity: readOnly || server.status === 'RETIRED' ? 0.5 : 1,
              }}
            >
              Retire
            </button>
          )}
        </div>
      </header>

      {retireError !== null && <ErrorState result={retireError} />}

      <h2 className="mt-6 mb-2 font-medium">Overview</h2>
      <dl className="text-sm">
        <Row label="Environment">{server.environment}</Row>
        <Row label="Operating system">
          {server.operating_system}
          {server.os_version !== null && ` ${server.os_version}`}
        </Row>
        <Row label="Cloud account">{server.cloud_account_id}</Row>
        <Row label="Provider resource id">{server.provider_resource_id}</Row>
        <Row label="Region">
          <Optional value={server.region} />
        </Row>
        <Row label="Private IP">
          <Optional value={server.private_ip} />
        </Row>
        <Row label="Owner team">
          <Optional value={server.owner_team} />
        </Row>
        <Row label="Management method">{server.management_method}</Row>
        <Row label="Monitoring">{server.monitoring_enabled ? 'Enabled' : 'Disabled'}</Row>
        <Row label="Metrics instance">
          <Optional value={server.metrics_instance} />
        </Row>
        <Row label="Registered">{server.registered_at}</Row>
        <Row label="Last seen">
          {/* Never seen is not the same as stale, and neither is a zero. */}
          {server.last_seen_at ?? <span style={{ color: 'var(--c-text-muted)' }}>Never seen</span>}
        </Row>
        <Row label="Stale">
          {server.stale === null ? (
            <span style={{ color: 'var(--c-text-muted)' }}>Unknown — never seen</span>
          ) : (
            String(server.stale)
          )}
        </Row>
        <Row label="Health">
          {server.health.state ?? <DataStateBanner section={server.health} />}
        </Row>
        <Row label="Open alerts">
          {/* `== null` catches both: the generated type makes a field with a
              Pydantic default optional, so "absent" and "null" are the same
              unknown and must render the same way. */}
          {server.open_alerts.counts == null ? (
            <DataStateBanner section={server.open_alerts} />
          ) : (
            `${String(server.open_alerts.counts.critical)} critical, ${String(server.open_alerts.counts.warning)} warning`
          )}
        </Row>
        <Row label="Registered services">{server.registered_service_count}</Row>
        <Row label="Management capabilities">
          {server.management_capabilities.capabilities == null ? (
            <DataStateBanner section={server.management_capabilities} />
          ) : (
            Object.entries(server.management_capabilities.capabilities)
              .filter(([, supported]) => supported)
              .map(([name]) => name)
              .join(', ')
          )}
        </Row>
      </dl>

      <h2 className="mt-6 mb-2 font-medium">Applications</h2>

      {associationError !== null && <ErrorState result={associationError} />}

      {server.applications.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          No applications are associated with this server.
        </p>
      ) : (
        <ul className="text-sm">
          {server.applications.map((application) => (
            <li
              key={application.id}
              className="flex items-center justify-between gap-3 border-t py-2"
              style={{ borderColor: 'var(--c-border)' }}
            >
              <span>
                <Link to={`/applications/${application.id}`} className="underline">
                  {application.name}
                </Link>
                {application.role_on_server !== null && ` — ${application.role_on_server}`}
              </span>
              {mayWrite && (
                <button
                  type="button"
                  disabled={readOnly}
                  onClick={() => void remove(application.id)}
                  className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Hidden for usability only (docs/SECURITY.md §6): §14.6 and §14.7
          require `server:write`, and the backend checks it on every request
          whatever this page renders. */}
      {mayWrite && !readOnly && (
        <AssociateApplication
          serverId={server.id}
          onAssociated={() => {
            setAssociationError(null);
            void load();
          }}
        />
      )}
    </section>
  );
}
