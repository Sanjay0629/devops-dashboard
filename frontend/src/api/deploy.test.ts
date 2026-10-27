import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CANCELLABLE,
  EMPTY_FILTERS,
  STATUSES,
  STATUS_TEXT,
  TERMINAL,
  deploymentQuery,
  failureText,
  pollErrorText,
  refusalText,
  requestDeployment,
  type DeploymentRefusal,
} from '@/api/deploy';

const ACCEPTED = {
  deployment_id: 'd-1',
  execution_status: 'REQUESTED',
  approval_status: 'NOT_REQUIRED',
  target: {},
  approval: null,
  deadline_at: '2026-10-27T10:00:00Z',
  correlation_id: 'c-1',
  poll_after_seconds: 5,
  idempotent_replay: false,
};

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

function refused(over: Partial<DeploymentRefusal>): DeploymentRefusal {
  return {
    kind: 'refused',
    status: 503,
    code: 'not_configured',
    reason: null,
    message: 'the backend’s own words',
    patterns: [],
    ...over,
  };
}

describe('requestDeployment', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends exactly four values and the idempotency key, and nothing a caller could add to', async () => {
    const fetchMock = respond(202, ACCEPTED);
    vi.stubGlobal('fetch', fetchMock);
    const result = await requestDeployment('app-1', 'staging', 'main', 'CR-1', 'key-0000001');
    expect(result.kind).toBe('ok');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/deployments');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      application_id: 'app-1',
      environment: 'staging',
      ref: 'main',
      reason: 'CR-1',
    });
    expect(new Headers(init.headers).get('Idempotency-Key')).toBe('key-0000001');
  });

  it('leaves the reason out when there is none, rather than sending an empty one', async () => {
    const fetchMock = respond(202, ACCEPTED);
    vi.stubGlobal('fetch', fetchMock);
    await requestDeployment('app-1', 'staging', 'main', null, 'key-0000001');
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const sent = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(Object.keys(sent)).toEqual(['application_id', 'environment', 'ref']);
  });

  it('has no parameter through which a url, a job or a build parameter could travel', () => {
    expect(requestDeployment.length).toBeLessThanOrEqual(5);
  });

  it('reports a replay as a replay', async () => {
    vi.stubGlobal('fetch', respond(200, { ...ACCEPTED, idempotent_replay: true }));
    const result = await requestDeployment('a', 'staging', 'main', null);
    expect(result).toMatchObject({ kind: 'ok', replayed: true });
  });

  it('keeps a deliberate 503 a refusal with its closed reason, not "could not reach the backend"', async () => {
    vi.stubGlobal(
      'fetch',
      respond(503, {
        error: {
          code: 'not_configured',
          message: 'x',
          details: { reason: 'approval_workflow_unavailable' },
        },
      }),
    );
    const result = await requestDeployment('a', 'production', 'main', 'CR');
    expect(result).toMatchObject({
      kind: 'refused',
      status: 503,
      code: 'not_configured',
      reason: 'approval_workflow_unavailable',
    });
  });

  it('carries the allowed ref patterns of a refused ref', async () => {
    vi.stubGlobal(
      'fetch',
      respond(422, {
        error: {
          code: 'not_permitted',
          message: 'x',
          details: { reason: 'ref_not_permitted', allowed_ref_patterns: ['^main$', 3, '^rel/.+$'] },
        },
      }),
    );
    const result = await requestDeployment('a', 'staging', 'develop', null);
    expect(result).toMatchObject({ kind: 'refused', patterns: ['^main$', '^rel/.+$'] });
  });

  it.each([
    [403, 'forbidden'],
    [404, 'not_found'],
    [423, 'read_only'],
  ])('maps %i to %s', async (status, kind) => {
    vi.stubGlobal('fetch', respond(status, {}));
    expect((await requestDeployment('a', 'staging', 'main', null)).kind).toBe(kind);
  });

  it.each([
    [409, 'operation_in_progress'],
    [429, 'rate_limited'],
    [422, 'not_registered'],
  ])('keeps %i as a refusal named %s', async (status, code) => {
    vi.stubGlobal('fetch', respond(status, { error: { code, message: 'm' } }));
    expect(await requestDeployment('a', 'staging', 'main', null)).toMatchObject({
      kind: 'refused',
      code,
    });
  });

  it('is invalid, not refused, for a 422 validation failure', async () => {
    vi.stubGlobal(
      'fetch',
      respond(422, { error: { code: 'validation_failed', message: 'bad ref' } }),
    );
    expect(await requestDeployment('a', 'staging', '..', null)).toMatchObject({
      kind: 'invalid',
      message: 'bad ref',
    });
  });

  it('says it could not reach the backend when it could not', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    expect(await requestDeployment('a', 'staging', 'main', null)).toEqual({
      kind: 'unreachable',
      reason: 'network down',
    });
  });

  it('does not trust a 202 with the wrong shape', async () => {
    vi.stubGlobal('fetch', respond(202, { something: 'else' }));
    expect((await requestDeployment('a', 'staging', 'main', null)).kind).toBe('unreachable');
  });
});

describe('deploymentQuery', () => {
  it('repeats a parameter per value and sends only what was given', () => {
    expect(
      deploymentQuery({
        ...EMPTY_FILTERS,
        statuses: ['FAILED', 'RUNNING'],
        environments: ['staging'],
        applicationId: 'app-1',
        requestedBy: 'u-1',
        from: '2026-10-01T00:00:00.000Z',
        cursor: 'c1',
      }),
    ).toBe(
      '?execution_status=FAILED&execution_status=RUNNING&environment=staging&application_id=app-1' +
        '&requested_by=u-1&from=2026-10-01T00%3A00%3A00.000Z&cursor=c1',
    );
  });

  it('is empty when nothing is set, so the backend applies its own window', () => {
    expect(deploymentQuery(EMPTY_FILTERS)).toBe('');
  });
});

describe('what a status and a failure mean', () => {
  it('has a sentence for every status and the terminal set is the four end states', () => {
    for (const status of STATUSES) expect(STATUS_TEXT[status]).not.toBe('');
    expect([...TERMINAL].sort()).toEqual(['CANCELLED', 'FAILED', 'ROLLED_BACK', 'SUCCEEDED']);
  });

  it('offers a cancel only before a worker has claimed it', () => {
    expect(CANCELLABLE).toEqual(['REQUESTED']);
  });

  it.each([
    'PERMISSION_REVOKED',
    'APPROVAL_REQUIRED',
    'TARGET_VALIDATION_FAILED',
    'TARGET_CHANGED',
    'REF_NOT_PERMITTED',
    'NOT_CONFIGURED',
    'JENKINS_UNAVAILABLE',
    'JENKINS_AUTHENTICATION',
    'JOB_NOT_FOUND',
    'TRIGGER_OUTCOME_UNKNOWN',
    'QUEUE_ITEM_LOST',
    'PIPELINE_FAILED',
    'PIPELINE_UNSTABLE',
    'PIPELINE_ABORTED',
    'PIPELINE_NOT_BUILT',
    'DEPLOYMENT_TIMED_OUT',
    'EXPIRED_BEFORE_START',
  ])('has words for %s, not the raw code', (code) => {
    expect(failureText(code)).not.toBe(code);
    expect(failureText(code).length).toBeGreaterThan(10);
  });

  it('shows an unknown code as itself rather than hiding it, and nothing as nothing', () => {
    expect(failureText('SOMETHING_NEW')).toBe('SOMETHING_NEW');
    expect(failureText(null)).toBe('');
    expect(failureText('')).toBe('');
  });

  it('says a failure whose build may exist is not a plain failure', () => {
    for (const code of ['TRIGGER_OUTCOME_UNKNOWN', 'QUEUE_ITEM_LOST', 'DEPLOYMENT_TIMED_OUT']) {
      expect(failureText(code)).toMatch(/may/);
    }
  });

  it('says a failure that sent nothing says nothing was sent', () => {
    for (const code of ['PERMISSION_REVOKED', 'TARGET_CHANGED', 'EXPIRED_BEFORE_START']) {
      expect(failureText(code)).toMatch(/nothing was sent/i);
    }
  });

  it('words a closed Jenkins reason and shows an unknown one as itself', () => {
    expect(pollErrorText('provider_unreachable')).toBe('Jenkins could not be reached.');
    expect(pollErrorText('mystery')).toBe('mystery');
    expect(pollErrorText(null)).toBe('');
  });
});

describe('refusalText', () => {
  it('names the approval workflow when that is why', () => {
    expect(refusalText(refused({ reason: 'approval_workflow_unavailable' }))).toMatch(
      /approval workflow is not available yet/,
    );
  });

  it('lists the patterns a refused ref was checked against', () => {
    const text = refusalText(
      refused({
        status: 422,
        code: 'not_permitted',
        reason: 'ref_not_permitted',
        patterns: ['^main$', '^release/.+$'],
      }),
    );
    expect(text).toContain('^main$, ^release/.+$');
  });

  it.each([
    [{ code: 'operation_in_progress', status: 409 }, /already pending or running/],
    [{ code: 'rate_limited', status: 429 }, /last hour/],
    [{ code: 'not_registered', status: 422 }, /No active deployment target/],
    [{ reason: 'jenkins_not_configured' }, /Jenkins is not configured/],
    [{ reason: 'demonstration_mode' }, /demonstration mode/],
    [
      { code: 'conflict', status: 409, reason: 'idempotency_key_reused_with_different_payload' },
      /conflicts with one already sent/,
    ],
  ])('words %j', (over, expected) => {
    expect(refusalText(refused(over))).toMatch(expected);
  });

  it('falls back to the backend’s own message rather than hiding it', () => {
    expect(refusalText(refused({ code: 'something_new', reason: 'unheard_of' }))).toBe(
      'the backend’s own words',
    );
  });
});
