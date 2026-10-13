import { useCallback, useEffect, useState } from 'react';

import { fetchReadiness, type ProbeState } from '@/api/health';

/**
 * The backend's own health, honestly reported.
 *
 * The shape matters more than the content. It establishes the rule that every
 * later page follows (PRD §4.2, docs/API_CONTRACTS.md §4): unknown is rendered
 * as unknown. There is no code path here that turns "no answer" into a
 * reassuring default — an unreachable backend says so.
 *
 * Moved out of `App` in M1.3, which took over the composition-root job of
 * gating on the session.
 */

const STATE_LABEL: Record<ProbeState['kind'], string> = {
  loading: 'Checking…',
  ok: 'Ready',
  unready: 'Not ready',
  unreachable: 'Unreachable',
};

const STATE_COLOUR: Record<ProbeState['kind'], string> = {
  loading: 'var(--c-unknown)',
  ok: 'var(--c-healthy)',
  unready: 'var(--c-warning)',
  unreachable: 'var(--c-critical)',
};

/** Colour is never the only signal (PRD §8), so each state carries a glyph too. */
const STATE_GLYPH: Record<ProbeState['kind'], string> = {
  loading: '…',
  ok: '✓',
  unready: '!',
  unreachable: '×',
};

function CheckRow({ name, passed }: { name: string; passed: boolean }): React.JSX.Element {
  return (
    <div
      className="flex items-center justify-between border-t px-4 py-2"
      style={{ borderColor: 'var(--c-border)' }}
    >
      <code className="text-sm">{name}</code>
      <span
        className="flex items-center gap-2 text-sm font-medium"
        style={{ color: passed ? 'var(--c-healthy)' : 'var(--c-critical)' }}
      >
        <span aria-hidden="true">{passed ? '✓' : '×'}</span>
        {passed ? 'pass' : 'fail'}
      </span>
    </div>
  );
}

export function BackendStatus(): React.JSX.Element {
  const [state, setState] = useState<ProbeState>({ kind: 'loading' });
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setState({ kind: 'loading' });
    const next = await fetchReadiness();
    setState(next);
    setCheckedAt(new Date());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const readiness = state.kind === 'ok' || state.kind === 'unready' ? state.readiness : null;

  return (
    <section
      className="rounded-lg border"
      style={{ background: 'var(--c-surface)', borderColor: 'var(--c-border)' }}
      aria-labelledby="backend-status-heading"
    >
      <div className="flex items-center justify-between px-4 py-3">
        <h2 id="backend-status-heading" className="font-medium">
          Backend status
        </h2>
        <span
          className="flex items-center gap-2 font-semibold"
          style={{ color: STATE_COLOUR[state.kind] }}
          role="status"
        >
          <span aria-hidden="true">{STATE_GLYPH[state.kind]}</span>
          {STATE_LABEL[state.kind]}
        </span>
      </div>

      {readiness !== null &&
        Object.entries(readiness.checks).map(([name, passed]) => (
          <CheckRow key={name} name={name} passed={passed} />
        ))}

      {state.kind === 'unreachable' && (
        <p
          className="border-t px-4 py-3 text-sm"
          style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
        >
          No response from the backend. Dependency status is <strong>unknown</strong> — not healthy,
          and not zero.
        </p>
      )}

      <div
        className="flex items-center justify-between border-t px-4 py-3 text-sm"
        style={{ borderColor: 'var(--c-border)', color: 'var(--c-text-muted)' }}
      >
        <span>
          {checkedAt === null ? 'Not yet checked' : `Checked at ${checkedAt.toISOString()}`}
        </span>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={state.kind === 'loading'}
          className="rounded border px-3 py-1 disabled:opacity-50"
          style={{ borderColor: 'var(--c-border)', color: 'var(--c-text)' }}
        >
          Refresh
        </button>
      </div>
    </section>
  );
}
