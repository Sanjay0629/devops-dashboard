/**
 * One service: a live reading (§16.4).
 *
 * Unlike the list, this asks Prometheus now, so it is the one place a restart
 * count and an activation time can appear.
 *
 * **Four fields are always blank here, and the page says so rather than
 * leaving a gap that looks like a loading problem** (`OPEN-29`): `sub_state`,
 * `load_state`, the enabled state and the main PID are not exposed by
 * `node_exporter`'s systemd collector or by `systemd_exporter`, so each reads
 * "Not collected by this source". Last state change is the same, and the
 * exporter's start time is shown under its own name because it describes
 * activation, not the last change of state.
 *
 * `null` is unknown and is never rendered as `0`, `false` or an empty string.
 * Logs (§16.7, §19.1) are one link away rather than a panel here: the link opens the
 * log search pre-filled with this server and unit, through the router's history
 * state and not the URL (`OPEN-20`, `OPEN-38`), and the search says what it found
 * or why it could not.
 */

import { Link, useParams } from 'react-router-dom';

import { readService } from '@/api/services';
import { formatInstant } from '@/components/chart/ticks';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { NotCollected, ServiceStatus } from '@/components/ServiceStatus';
import { useLatestResponse } from '@/components/useLatestResponse';

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

function Unknown({ children = 'Unknown' }: { children?: string }): React.JSX.Element {
  return <span style={{ color: 'var(--c-text-muted)' }}>{children}</span>;
}

function yesNo(value: boolean | null): React.JSX.Element {
  if (value === null) return <Unknown />;
  return <>{value ? 'Yes' : 'No'}</>;
}

export function ServiceDetailPage(): React.JSX.Element {
  const { serverId = '', serviceName = '' } = useParams();
  const { value: result, reload } = useLatestResponse(
    () => readService(serverId, serviceName),
    [serverId, serviceName],
  );

  if (result === null) return <LoadingState what="this service" />;
  if (result.kind !== 'ok') return <ErrorState result={result} onRetry={() => void reload()} />;

  const service = result.value;

  return (
    <section aria-labelledby="service-heading">
      <p className="mb-2 text-sm">
        <Link to={`/servers/${service.server_id}/services`} className="underline">
          ← Services on {service.server_display_name}
        </Link>
      </p>
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="service-heading" className="text-xl font-semibold">
            {service.service_name}
          </h1>
          {service.display_name !== null && (
            <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
              {service.display_name}
            </p>
          )}
        </div>
        <ServiceStatus item={service} />
      </header>

      <p className="mb-4 text-sm">
        <Link
          to="/logs"
          state={{
            servers: [{ id: service.server_id, label: service.server_display_name }],
            service: service.service_name,
          }}
          className="underline"
        >
          View logs for {service.service_name}
        </Link>
      </p>

      <dl className="text-sm">
        <Row label="Server">
          <Link to={`/servers/${service.server_id}`} className="underline">
            {service.server_display_name}
          </Link>
        </Row>
        <Row label="Environment">{service.environment}</Row>
        <Row label="Host reachable">{yesNo(service.server_reachable)}</Row>
        <Row label="Monitoring enabled">{yesNo(service.monitoring_enabled)}</Row>
        <Row label="Restart count">
          {service.restart_count === null ? <Unknown /> : service.restart_count}
        </Row>
        <Row label="Active since">
          {service.started_at == null ? (
            <Unknown>Not running, or not reported</Unknown>
          ) : (
            formatInstant(new Date(service.started_at).getTime())
          )}
        </Row>
        <Row label="Sub state">{service.sub_state ?? <NotCollected />}</Row>
        <Row label="Load state">{service.load_state ?? <NotCollected />}</Row>
        <Row label="Enabled at boot">
          {service.enabled === null ? <NotCollected /> : yesNo(service.enabled)}
        </Row>
        <Row label="Main process ID">{service.main_pid ?? <NotCollected />}</Row>
        <Row label="Last state change">
          {service.last_state_change_at == null ? (
            <NotCollected />
          ) : (
            formatInstant(new Date(service.last_state_change_at).getTime())
          )}
        </Row>
      </dl>
    </section>
  );
}
