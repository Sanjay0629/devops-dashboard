/**
 * Backend health client.
 *
 * Deliberately hand-written and tiny. From M1.5 onward, API types are
 * *generated* from the backend OpenAPI schema and committed
 * (docs/TECH_STACK.md §11) — this file is the one exception, because it exists
 * before there is a schema to generate from.
 */

export type DependencyChecks = Record<string, boolean>;

export interface Readiness {
  status: string;
  checks: DependencyChecks;
}

/** What the UI knows, as opposed to what it assumes. */
export type ProbeState =
  | { kind: 'loading' }
  | { kind: 'ok'; readiness: Readiness }
  | { kind: 'unready'; readiness: Readiness }
  | { kind: 'unreachable'; reason: string };

const REQUEST_TIMEOUT_MS = 5000;

function isReadiness(value: unknown): value is Readiness {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<Readiness>;
  return typeof candidate.status === 'string' && typeof candidate.checks === 'object';
}

/**
 * Fetch readiness.
 *
 * Never throws: an unreachable backend is a *state to display*, not an
 * exception to swallow. A 503 is a real answer (the backend is up and telling
 * us a dependency is down) and is distinct from no answer at all.
 */
export async function fetchReadiness(signal?: AbortSignal): Promise<ProbeState> {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  signal?.addEventListener('abort', () => {
    controller.abort();
  });

  try {
    const response = await fetch('/readyz', {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    const body: unknown = await response.json();
    if (!isReadiness(body)) {
      return { kind: 'unreachable', reason: 'Unexpected response shape' };
    }
    return response.ok ? { kind: 'ok', readiness: body } : { kind: 'unready', readiness: body };
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  } finally {
    clearTimeout(timer);
  }
}
