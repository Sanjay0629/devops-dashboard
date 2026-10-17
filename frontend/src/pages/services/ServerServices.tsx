/**
 * The services panel for one server (§16.2) as its own route, beside the
 * server's metrics page rather than inside its overview: the overview is
 * inventory (a stored row), and this is an observation with a collection time.
 *
 * The server's name is read for the heading only. A server the caller cannot
 * see is a `404` from that read, and the list below is never rendered for it —
 * the list endpoint answers `404` for the same reason, so the two agree.
 */

import { Link, useParams } from 'react-router-dom';

import { readServer } from '@/api/inventory';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';
import { ServiceList } from '@/pages/services/ServiceList';

export function ServerServices(): React.JSX.Element {
  const { serverId = '' } = useParams();
  const { value: server } = useLatestResponse(() => readServer(serverId), [serverId]);

  if (server === null) return <LoadingState what="this server" />;
  if (server.kind !== 'ok') return <ErrorState result={server} />;

  return (
    <div>
      <p className="mb-2 text-sm">
        <Link to={`/servers/${serverId}`} className="underline">
          ← {server.value.display_name}
        </Link>
      </p>
      <ServiceList serverId={serverId} serverName={server.value.display_name} />
    </div>
  );
}
