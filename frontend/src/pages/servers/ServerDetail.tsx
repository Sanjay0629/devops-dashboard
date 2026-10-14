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
  readServer,
  retireServer,
  type Result,
  type ServerDetail as ServerDetailShape,
} from '@/api/inventory';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { Failure, Loading, NotCollected, Status } from '@/pages/inventory/states';

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

export function ServerDetail(): React.JSX.Element {
  const { serverId = '' } = useParams();
  const navigate = useNavigate();
  const [result, setResult] = useState<Result<ServerDetailShape> | null>(null);
  const [retireError, setRetireError] = useState<Exclude<Result<null>, { kind: 'ok' }> | null>(
    null,
  );
  const mayWrite = useCanRender(PERMISSIONS.serverWrite);
  const readOnly = useIsReadOnly();

  const load = useCallback(async () => {
    setResult(await readServer(serverId));
  }, [serverId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (result === null) return <Loading what="this server" />;
  if (result.kind !== 'ok') return <Failure result={result} />;

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
          <Status value={server.status} />
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

      {retireError !== null && <Failure result={retireError} />}

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
        <Row label="Health">{server.health.state ?? <NotCollected section={server.health} />}</Row>
        <Row label="Open alerts">
          {server.open_alerts.counts === null ? (
            <NotCollected section={server.open_alerts} />
          ) : (
            `${String(server.open_alerts.counts.critical)} critical, ${String(server.open_alerts.counts.warning)} warning`
          )}
        </Row>
        <Row label="Registered services">{server.registered_service_count}</Row>
        <Row label="Management capabilities">
          {server.management_capabilities.capabilities === null ? (
            <NotCollected section={server.management_capabilities} />
          ) : (
            Object.entries(server.management_capabilities.capabilities)
              .filter(([, supported]) => supported)
              .map(([name]) => name)
              .join(', ')
          )}
        </Row>
      </dl>

      <h2 className="mt-6 mb-2 font-medium">Applications</h2>
      {server.applications.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          No applications are associated with this server.
        </p>
      ) : (
        <ul className="text-sm">
          {server.applications.map((application) => (
            <li
              key={application.id}
              className="border-t py-2"
              style={{ borderColor: 'var(--c-border)' }}
            >
              <Link to={`/applications/${application.id}`} className="underline">
                {application.name}
              </Link>
              {application.role_on_server !== null && ` — ${application.role_on_server}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
