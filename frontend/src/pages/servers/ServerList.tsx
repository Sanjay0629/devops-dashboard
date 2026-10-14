/**
 * Server inventory list with filters (§14.1).
 *
 * Filters are sent to the backend rather than applied to a fetched page:
 * the collection is scope-filtered and cursor-paged server-side, so a
 * client-side filter would be filtering one page of someone else's answer.
 *
 * "Register server" is hidden without `server:write` and disabled in
 * read-only mode. Both are **usability**, not control — the backend answers
 * `403` and `423` on its own account (docs/SECURITY.md §6).
 */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listServers, type Page, type Result, type ServerSummary } from '@/api/inventory';
import { PERMISSIONS } from '@/auth/permissions';
import { useCanRender, useIsReadOnly } from '@/auth/useSession';
import { Empty, Failure, Loading, Status } from '@/pages/inventory/states';

const ENVIRONMENTS = ['development', 'staging', 'production', 'other'];
const STATUSES = ['PENDING', 'MANAGED', 'STALE', 'UNREACHABLE', 'RETIRED'];

export function ServerList(): React.JSX.Element {
  const [environment, setEnvironment] = useState('');
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [result, setResult] = useState<Result<Page<ServerSummary>> | null>(null);
  const mayWrite = useCanRender(PERMISSIONS.serverWrite);
  const readOnly = useIsReadOnly();

  const load = useCallback(async () => {
    setResult(null);
    setResult(await listServers({ environment, status, q: q.trim() || undefined }));
  }, [environment, status, q]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="servers-heading">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 id="servers-heading" className="text-xl font-semibold">
          Servers
        </h1>
        {mayWrite && (
          <Link
            to="/servers/new"
            aria-disabled={readOnly}
            className="rounded border px-3 py-1 text-sm"
            style={{
              borderColor: 'var(--c-border)',
              opacity: readOnly ? 0.5 : 1,
              pointerEvents: readOnly ? 'none' : undefined,
            }}
          >
            Register server
          </Link>
        )}
      </header>

      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void load();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Environment
          </span>
          <select
            value={environment}
            onChange={(event) => {
              setEnvironment(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {ENVIRONMENTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Status
          </span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          >
            <option value="">All</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
            Search
          </span>
          <input
            type="search"
            value={q}
            placeholder="hostname, name or resource id"
            onChange={(event) => {
              setQ(event.target.value);
            }}
            className="rounded border px-2 py-1"
            style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
          />
        </label>

        <button
          type="submit"
          className="rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Apply
        </button>
      </form>

      {result === null && <Loading what="servers" />}
      {result?.kind === 'ok' && result.value.items.length === 0 && (
        <Empty message="No servers match these filters, or none are within your access scope." />
      )}
      {result !== null && result.kind !== 'ok' && <Failure result={result} />}

      {result?.kind === 'ok' && result.value.items.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr style={{ color: 'var(--c-text-muted)' }}>
              <th scope="col" className="py-2">
                Name
              </th>
              <th scope="col">Hostname</th>
              <th scope="col">Environment</th>
              <th scope="col">Status</th>
              <th scope="col">Owner</th>
            </tr>
          </thead>
          <tbody>
            {result.value.items.map((server) => (
              <tr key={server.id} className="border-t" style={{ borderColor: 'var(--c-border)' }}>
                <td className="py-2">
                  <Link to={`/servers/${server.id}`} className="underline">
                    {server.display_name}
                  </Link>
                </td>
                <td>{server.hostname}</td>
                <td>{server.environment}</td>
                <td>
                  <Status value={server.status} />
                </td>
                {/* An owner that was never recorded is unknown, not "—". */}
                <td
                  style={server.owner_team === null ? { color: 'var(--c-text-muted)' } : undefined}
                >
                  {server.owner_team ?? 'Not recorded'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {result?.kind === 'ok' && result.value.page.has_more && (
        <p className="mt-3 text-sm" style={{ color: 'var(--c-text-muted)' }}>
          More results exist. Narrow the filters to see them — paging through the list arrives with
          the dashboard shell.
        </p>
      )}
    </section>
  );
}
