/**
 * Log search (§19.1, PRD §4.7).
 *
 * **The form builds a structured filter and nothing else.** There is no query box:
 * the backend renders the selector and the line filter itself (AD-05), scoped to
 * the servers the caller may read, so a field here that accepted LogQL would be a
 * way to ask for something the server then has to refuse. The one free-text field,
 * "contains", is a literal substring.
 *
 * What the page owes the operator, in order of how badly it can go wrong:
 *
 * 1. **Unavailable is not empty.** A log store that could not answer is its own
 *    state with its own words (`UnavailableNotice`), never an empty table.
 * 2. **Empty is not "no errors".** See `LogResults`: the per-server ingestion
 *    states decide what an empty page may say.
 * 3. **Scope is said, not hidden.** A requested server that was dropped is counted
 *    in the summary.
 * 4. **Lines are text.** See `LogResults`.
 *
 * Filters never go into the URL (`OPEN-20`): a pre-fill from another page arrives
 * in the router's history state (`prefill.ts`).
 */

import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';

import { listApplications, listServers } from '@/api/inventory';
import {
  readLogSources,
  searchLogs,
  type LogEntry,
  type LogQueryRequest,
  type LogResult,
  type LogSearchResponse,
} from '@/api/logs';
import { EmptyState, LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useLatestResponse } from '@/components/useLatestResponse';
import { LogResults, UnavailableNotice } from '@/pages/logs/LogResults';
import {
  BLANK,
  ENVIRONMENTS,
  LIMITS,
  MAX_SERVERS,
  bodyOf,
  hasTarget,
  type Form,
} from '@/pages/logs/form';
import { LOG_RANGES, isLogsPrefill } from '@/pages/logs/prefill';

const fieldStyle = { borderColor: 'var(--c-border)', background: 'var(--c-surface)' };

function Labelled({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <label className="text-sm">
      <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
        {label}
      </span>
      {children}
    </label>
  );
}

export function LogSearch(): React.JSX.Element {
  const { state } = useLocation() as { state: unknown };
  const prefill = isLogsPrefill(state) ? state : null;

  const [form, setForm] = useState<Form>(() =>
    prefill === null
      ? BLANK
      : {
          ...BLANK,
          rangeId: prefill.rangeId ?? BLANK.rangeId,
          servers: Object.fromEntries(prefill.servers.map((s) => [s.id, s.label])),
          service: prefill.service ?? '',
        },
  );
  const [q, setQ] = useState('');
  const [first, setFirst] = useState<LogSearchResponse | null>(null);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [failure, setFailure] = useState<Exclude<LogResult<never>, { kind: 'ok' }> | null>(null);
  const [searching, setSearching] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searched, setSearched] = useState(false);
  // The body that produced the rows on screen, so "load older" continues *that*
  // query and not whatever the form has since been edited to.
  const shown = useRef<LogQueryRequest | null>(null);
  // A ticket per request: a response that is no longer the newest is discarded
  // (the lesson of `useLatestResponse`, which this page cannot use because it
  // accumulates pages).
  const ticket = useRef(0);

  const { value: sources } = useLatestResponse(readLogSources, []);
  const { value: candidates, loading: loadingServers } = useLatestResponse(
    () => listServers({ q: q.trim() || undefined }),
    [q],
  );
  const { value: applications } = useLatestResponse(() => listApplications({}), []);

  const catalogue = sources?.kind === 'ok' ? sources.value : null;
  const serverItems = candidates?.kind === 'ok' ? candidates.value.items : [];
  const applicationItems = applications?.kind === 'ok' ? applications.value.items : [];

  const run = async (body: LogQueryRequest, more: boolean): Promise<void> => {
    const mine = ++ticket.current;
    if (more) setLoadingMore(true);
    else {
      setSearching(true);
      setFailure(null);
    }
    const result = await searchLogs(body);
    if (mine !== ticket.current) return;
    setSearching(false);
    setLoadingMore(false);
    if (result.kind !== 'ok') {
      // A failed first search clears the table: stale rows under an error banner
      // would read as the answer. A failed "load older" keeps what was shown.
      if (!more) {
        setFirst(null);
        setEntries([]);
        setCursor(null);
      }
      setFailure(result);
      return;
    }
    setFailure(null);
    setSearched(true);
    setCursor(result.value.page.next_cursor);
    if (more) setEntries((current) => [...current, ...result.value.entries]);
    else {
      setFirst(result.value);
      setEntries(result.value.entries);
    }
  };

  const search = (): void => {
    if (!hasTarget(form)) return;
    const body = bodyOf(form);
    shown.current = body;
    void run(body, false);
  };

  const loadOlder = (): void => {
    if (shown.current === null || cursor === null) return;
    void run({ ...shown.current, cursor }, true);
  };

  // A pre-fill from another page searches once on arrival; typing does not.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefill !== null && !prefilled.current && hasTarget(form)) {
      prefilled.current = true;
      search();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleServer = (id: string, label: string): void => {
    setForm((current) => {
      const { [id]: removed, ...rest } = current.servers;
      if (removed !== undefined) return { ...current, servers: rest };
      if (Object.keys(current.servers).length >= MAX_SERVERS) return current;
      return { ...current, servers: { ...current.servers, [id]: label } };
    });
  };

  const set = <K extends keyof Form>(key: K, value: Form[K]): void => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  return (
    <section aria-labelledby="logs-heading">
      <h1 id="logs-heading" className="mb-4 text-xl font-semibold">
        Logs
      </h1>

      <form
        className="mb-6 flex flex-col gap-4 rounded border p-3"
        style={{ borderColor: 'var(--c-border)' }}
        onSubmit={(event) => {
          event.preventDefault();
          search();
        }}
      >
        <div className="flex flex-wrap items-end gap-4">
          <Labelled label="Time range">
            <select
              value={form.rangeId}
              onChange={(event) => {
                set('rangeId', event.target.value);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            >
              {LOG_RANGES.map((range) => (
                <option key={range.id} value={range.id}>
                  {range.label}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled label="Environment">
            <select
              value={form.environment}
              onChange={(event) => {
                set('environment', event.target.value as Form['environment']);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            >
              <option value="">Any</option>
              {(catalogue?.environments ?? ENVIRONMENTS).map((environment) => (
                <option key={environment} value={environment}>
                  {environment}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled label="Application">
            <select
              value={form.applicationId}
              onChange={(event) => {
                set('applicationId', event.target.value);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            >
              <option value="">Any</option>
              {applicationItems.map((application) => (
                <option key={application.id} value={application.id}>
                  {application.name}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled label="Source">
            <select
              value={form.sourceType}
              onChange={(event) => {
                set('sourceType', event.target.value as Form['sourceType']);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            >
              <option value="">Any I may read</option>
              {(catalogue?.source_types ?? []).map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled label="Common error pattern">
            <select
              value={form.preset}
              onChange={(event) => {
                set('preset', event.target.value as Form['preset']);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            >
              <option value="">None</option>
              {(catalogue?.presets ?? []).map((preset) => (
                <option key={preset.id} value={preset.id} title={preset.description}>
                  {preset.label}
                </option>
              ))}
            </select>
          </Labelled>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <Labelled label="Service or unit">
            <input
              type="text"
              value={form.service}
              maxLength={63}
              placeholder="e.g. nginx.service"
              onChange={(event) => {
                set('service', event.target.value);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            />
          </Labelled>
          <Labelled label="Contains (literal text)">
            <input
              type="text"
              value={form.contains}
              maxLength={catalogue?.limits.max_contains ?? 256}
              onChange={(event) => {
                set('contains', event.target.value);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            />
          </Labelled>
          <Labelled label="Lines per page">
            <select
              value={form.limit}
              onChange={(event) => {
                set('limit', Number(event.target.value));
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            >
              {LIMITS.map((limit) => (
                <option key={limit} value={limit}>
                  {limit}
                </option>
              ))}
            </select>
          </Labelled>
          <fieldset className="text-sm">
            <legend className="mb-1" style={{ color: 'var(--c-text-muted)' }}>
              Severity
            </legend>
            {(catalogue?.levels ?? []).map((level) => (
              <label key={level} className="mr-3">
                <input
                  type="checkbox"
                  checked={form.levels.includes(level)}
                  onChange={() => {
                    set(
                      'levels',
                      form.levels.includes(level)
                        ? form.levels.filter((entry) => entry !== level)
                        : [...form.levels, level],
                    );
                  }}
                  className="mr-1"
                />
                {level}
              </label>
            ))}
          </fieldset>
        </div>

        <div>
          <label className="mb-2 block text-sm">
            <span className="mb-1 block" style={{ color: 'var(--c-text-muted)' }}>
              Search servers
            </span>
            <input
              type="search"
              value={q}
              placeholder="name or hostname"
              onChange={(event) => {
                setQ(event.target.value);
              }}
              className="rounded border px-2 py-1"
              style={fieldStyle}
            />
          </label>
          {loadingServers && candidates === null && <LoadingState what="servers" />}
          {candidates !== null && candidates.kind !== 'ok' && <ErrorState result={candidates} />}
          {candidates?.kind === 'ok' && serverItems.length === 0 && (
            <EmptyState message="No servers match that search." />
          )}
          <ul className="m-0 grid max-h-48 list-none grid-cols-1 gap-1 overflow-auto p-0 text-sm sm:grid-cols-2">
            {serverItems.map((server) => (
              <li key={server.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={server.id in form.servers}
                    disabled={
                      !(server.id in form.servers) &&
                      Object.keys(form.servers).length >= MAX_SERVERS
                    }
                    onChange={() => {
                      toggleServer(server.id, server.display_name);
                    }}
                    className="mr-2"
                  />
                  {server.display_name}{' '}
                  <span style={{ color: 'var(--c-text-muted)' }}>({server.environment})</span>
                </label>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs" style={{ color: 'var(--c-text-muted)' }}>
            {Object.keys(form.servers).length} of {MAX_SERVERS} servers selected. Choose servers, an
            environment or an application: logs are never searched across the whole estate.
          </p>
        </div>

        <div>
          <button
            type="submit"
            disabled={!hasTarget(form) || searching}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            style={{ borderColor: 'var(--c-border)' }}
          >
            Search
          </button>
        </div>
      </form>

      {searching && <LoadingState what="logs" />}
      {failure !== null &&
        (failure.kind === 'unavailable' ? (
          <UnavailableNotice failure={failure} />
        ) : (
          <ErrorState result={failure} />
        ))}
      {!searching && first !== null && (
        <LogResults
          first={first}
          entries={entries}
          hasMore={cursor !== null}
          loadingMore={loadingMore}
          onLoadMore={loadOlder}
        />
      )}
      {!searching && !searched && failure === null && (
        <EmptyState message="Choose what to search and press Search." />
      )}
    </section>
  );
}
