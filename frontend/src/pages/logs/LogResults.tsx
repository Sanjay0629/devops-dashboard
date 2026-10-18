/**
 * The result of a log search, and what an empty one means.
 *
 * **An empty result is not "no errors".** During an incident the page is read
 * for exactly one thing — is anything wrong? — and "no lines" answers it wrongly
 * whenever the host is silent, the shipper cannot read its files, or the log store
 * could not be asked. So an empty page says "no matching lines" **only when every
 * selected server has sent logs recently**; otherwise it names the servers that
 * have not, and what that may mean (PROGRESS issue 93, `OPEN-34`).
 *
 * **Every line is rendered as text.** A log line is untrusted input (it can hold
 * markup, from an attacker who can make an application log it), and React renders
 * a string child as a text node. There is no `dangerouslySetInnerHTML` in this
 * file and a test pins that, with markup in a line.
 */

import { Link } from 'react-router-dom';

import type {
  IngestionState,
  LogEntry,
  LogSearchResponse,
  LogUnavailable,
  ServerIngestion,
} from '@/api/logs';
import { EmptyState } from '@/components/EmptyState';
import { stampOf } from '@/pages/logs/format';

const STATE_TEXT: Record<Exclude<IngestionState, 'receiving'>, string> = {
  quiet: 'has sent no logs in the last 15 minutes',
  silent:
    'has sent no logs in the last 24 hours — a quiet host, or a shipper that cannot read its files',
  unknown: 'could not be checked just now',
};

export function UnavailableNotice({ failure }: { failure: LogUnavailable }): React.JSX.Element {
  const text =
    failure.code === 'not_configured'
      ? 'Log search is not configured on this deployment, so no logs can be shown.'
      : failure.code === 'provider_timeout'
        ? 'The log store did not answer in time, so no logs can be shown.'
        : 'The log store is unavailable, so no logs can be shown.';
  return (
    <div
      role="alert"
      className="rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-critical)', color: 'var(--c-critical)' }}
    >
      <p className="font-medium">{text}</p>
      <p className="mt-1">
        This is not an absence of logs. The logs may hold exactly what you are looking for.
      </p>
    </div>
  );
}

function IngestionList({ items }: { items: ServerIngestion[] }): React.JSX.Element {
  return (
    <ul className="m-0 mt-1 list-disc pl-5">
      {items.map((item) => (
        <li key={item.server_id}>
          {item.hostname} {STATE_TEXT[item.state as Exclude<IngestionState, 'receiving'>]}
        </li>
      ))}
    </ul>
  );
}

function Notices({
  response,
  shown,
}: {
  response: LogSearchResponse;
  shown: number;
}): React.JSX.Element | null {
  const silentOnes = response.ingestion.filter((item) => item.state !== 'receiving');

  if (response.servers_searched === 0) {
    return (
      <EmptyState message="None of the selected servers can be searched with your access, so nothing was queried." />
    );
  }
  if (shown === 0 && silentOnes.length === 0) {
    return (
      <EmptyState message="No lines match these filters. Every selected server has sent logs in the last 15 minutes." />
    );
  }
  if (silentOnes.length === 0) return null;
  return (
    <div
      role="status"
      className="rounded border px-4 py-3 text-sm"
      style={{ borderColor: 'var(--c-warning)', color: 'var(--c-warning)' }}
    >
      <p className="font-medium">
        {shown === 0
          ? 'No lines were returned. That does not mean there are no errors:'
          : 'Some selected servers may be missing from these results:'}
      </p>
      <IngestionList items={silentOnes} />
    </div>
  );
}

function LevelCell({ level }: { level: LogEntry['level'] }): React.JSX.Element {
  const colour =
    level === 'error' || level === 'critical'
      ? 'var(--c-critical)'
      : level === 'warn'
        ? 'var(--c-warning)'
        : 'var(--c-text-muted)';
  // The word is the signal; the colour only reinforces it.
  return (
    <span className="rounded border px-1 text-xs" style={{ borderColor: colour, color: colour }}>
      {level}
    </span>
  );
}

export function LogResults({
  first,
  entries,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  /** The first page's response: it carries the counts and the ingestion states. */
  first: LogSearchResponse;
  entries: LogEntry[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}): React.JSX.Element {
  return (
    <div aria-live="polite">
      <p className="mb-2 text-xs" style={{ color: 'var(--c-text-muted)' }}>
        {entries.length} {entries.length === 1 ? 'line' : 'lines'} · {first.servers_searched}{' '}
        {first.servers_searched === 1 ? 'server' : 'servers'} searched
        {first.excluded_by_scope_count > 0 &&
          ` · ${String(first.excluded_by_scope_count)} requested ${
            first.excluded_by_scope_count === 1 ? 'server is' : 'servers are'
          } outside your access and ${first.excluded_by_scope_count === 1 ? 'was' : 'were'} not searched`}
        {hasMore && ' · more lines match than are shown'}
      </p>

      <div className="mb-3">
        <Notices response={first} shown={entries.length} />
      </div>

      {entries.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Log lines, newest first</caption>
            <thead>
              <tr style={{ color: 'var(--c-text-muted)' }}>
                <th scope="col" className="py-1 pr-3 font-normal">
                  Time
                </th>
                <th scope="col" className="py-1 pr-3 font-normal">
                  Server
                </th>
                <th scope="col" className="py-1 pr-3 font-normal">
                  Source
                </th>
                <th scope="col" className="py-1 pr-3 font-normal">
                  Service
                </th>
                <th scope="col" className="py-1 pr-3 font-normal">
                  Level
                </th>
                <th scope="col" className="py-1 font-normal">
                  Line
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, index) => (
                <tr
                  key={`${entry.timestamp}-${String(index)}`}
                  className="border-t align-top"
                  style={{ borderColor: 'var(--c-border)' }}
                >
                  <td className="whitespace-nowrap py-1 pr-3 text-xs">
                    <time dateTime={entry.timestamp} title={entry.timestamp}>
                      {stampOf(entry.timestamp)}
                    </time>
                  </td>
                  <td className="py-1 pr-3">
                    <Link to={`/servers/${entry.server_id}`} className="underline">
                      {entry.hostname}
                    </Link>
                  </td>
                  <td className="py-1 pr-3">{entry.source_type}</td>
                  <td className="py-1 pr-3">{entry.service}</td>
                  <td className="py-1 pr-3">
                    <LevelCell level={entry.level} />
                  </td>
                  <td className="py-1">
                    <code className="whitespace-pre-wrap break-all font-mono text-xs">
                      {entry.line}
                    </code>
                    {entry.line_truncated && (
                      <span className="ml-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
                        (line cut here)
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasMore && (
        <button
          type="button"
          onClick={onLoadMore}
          disabled={loadingMore}
          className="mt-3 rounded border px-3 py-1 text-sm disabled:opacity-50"
          style={{ borderColor: 'var(--c-border)' }}
        >
          {loadingMore ? 'Loading…' : 'Load older lines'}
        </button>
      )}
    </div>
  );
}
