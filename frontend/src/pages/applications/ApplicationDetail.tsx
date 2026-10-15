/**
 * Application detail (§18.3): metadata and the servers it runs on.
 *
 * An application is **environment-agnostic** (AD-12, confirmed 2026-10-14),
 * so there is no environment field to show. Its environments are a property
 * of the servers below, which is why they are grouped by environment rather
 * than listed flat — that grouping *is* the answer to "where does this run",
 * and §18.6's per-environment health will slot into the same shape.
 */

import { Link, useParams } from 'react-router-dom';

import { readApplication, type AssociatedServer } from '@/api/inventory';
import { DataStateBanner } from '@/components/DataStateBanner';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';
import { StatusBadge } from '@/components/StatusBadge';

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

function byEnvironment(servers: AssociatedServer[]): [string, AssociatedServer[]][] {
  const grouped = new Map<string, AssociatedServer[]>();
  for (const server of servers) {
    grouped.set(server.environment, [...(grouped.get(server.environment) ?? []), server]);
  }
  return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export function ApplicationDetail(): React.JSX.Element {
  const { applicationId = '' } = useParams();
  const { value: result } = useLatestResponse(
    () => readApplication(applicationId),
    [applicationId],
  );

  if (result === null) return <LoadingState what="this application" />;
  if (result.kind !== 'ok') return <ErrorState result={result} />;

  const application = result.value;

  return (
    <section aria-labelledby="application-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 id="application-heading" className="text-xl font-semibold">
            {application.name}
          </h1>
          <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
            {application.slug}
          </p>
        </div>
        <StatusBadge value={application.status} />
      </header>

      <dl className="text-sm">
        <Row label="Description">
          {application.description ?? (
            <span style={{ color: 'var(--c-text-muted)' }}>Not recorded</span>
          )}
        </Row>
        <Row label="Owner team">
          {application.owner_team ?? (
            <span style={{ color: 'var(--c-text-muted)' }}>Not recorded</span>
          )}
        </Row>
        <Row label="Repository">
          {application.repository_reference ?? (
            <span style={{ color: 'var(--c-text-muted)' }}>Not recorded</span>
          )}
        </Row>
        <Row label="Registered health checks">{application.health_check_config.checks.length}</Row>
        <Row label="Registered services">{application.registered_service_count}</Row>
        <Row label="Deployment targets">
          <DataStateBanner section={application.deployment_targets} />
        </Row>
      </dl>

      <h2 className="mt-6 mb-2 font-medium">Servers</h2>
      {application.servers.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          No servers are associated with this application.
        </p>
      ) : (
        byEnvironment(application.servers).map(([environment, servers]) => (
          <div key={environment} className="mb-4">
            <h3 className="mb-1 text-sm font-medium" style={{ color: 'var(--c-text-muted)' }}>
              {environment}
            </h3>
            <ul className="text-sm">
              {servers.map((server) => (
                <li
                  key={server.id}
                  className="flex items-center justify-between border-t py-2"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <Link to={`/servers/${server.id}`} className="underline">
                    {server.display_name}
                  </Link>
                  <StatusBadge value={server.status} />
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}
