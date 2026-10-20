import { beforeEach, describe, expect, it, vi } from 'vitest';

import { decide, decisionProblem, listApprovals, type Decided } from '@/api/approvals';

const reply = (status: number, body: unknown) =>
  Promise.resolve({
    ok: status < 400,
    status,
    json: () => (body === undefined ? Promise.reject(new Error('no body')) : Promise.resolve(body)),
  });

describe('decide', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the verb in the path and a body of nothing but the reason', async () => {
    const fetchMock = vi.fn().mockImplementation(() => reply(200, { operation_id: 'op-1' }));
    vi.stubGlobal('fetch', fetchMock);

    await decide('op-1', 'approve', null);
    await decide('op-1', 'reject', 'No');

    const [first, second] = fetchMock.mock.calls as [[string, RequestInit], [string, RequestInit]];
    expect(first[0]).toContain('/operations/op-1/approve');
    expect(JSON.parse(first[1].body as string)).toEqual({});
    expect(second[0]).toContain('/operations/op-1/reject');
    expect(JSON.parse(second[1].body as string)).toEqual({ reason: 'No' });
    expect(first[1].method).toBe('POST');
  });

  it('keeps the closed reason of a refusal, which a plain result would lose', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        reply(403, {
          error: {
            code: 'forbidden',
            message: 'm',
            details: { reason: 'self_approval_not_permitted' },
          },
        }),
      ),
    );
    expect(await decide('op-1', 'approve', null)).toEqual({
      kind: 'refused',
      status: 403,
      reason: 'self_approval_not_permitted',
      message: 'm',
    });
  });

  it('is not fooled by a 200 that is not an operation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => reply(200, { nope: true })),
    );
    expect((await decide('op-1', 'approve', null)).kind).toBe('unreachable');
  });

  it('reports a failed request as unreachable, never as a decision', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await decide('op-1', 'approve', null)).toEqual({
      kind: 'unreachable',
      reason: 'offline',
    });
  });

  it('asks for the queue with the decision as a query parameter', async () => {
    const fetchMock = vi.fn().mockImplementation(() => reply(200, { items: [], page: {} }));
    vi.stubGlobal('fetch', fetchMock);
    await listApprovals('EXPIRED', 'abc');
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/approvals\?decision=EXPIRED&cursor=abc/,
    );
  });
});

describe('decisionProblem', () => {
  const refused = (status: 403 | 409 | 422, reason: string | null): Decided => ({
    kind: 'refused',
    status,
    reason,
    message: 'the backend’s words',
  });
  const problem = (result: Decided): string => {
    if (result.kind === 'ok') throw new Error('not a problem');
    return decisionProblem(result);
  };

  it.each([
    [403, 'self_approval_not_permitted', /does not allow you to approve your own/i],
    [403, 'approver_role_required', /needs a role you do not hold/i],
    [409, 'approval_expired', /window for this request has passed/i],
    [409, 'not_awaiting_approval', /no longer waiting for a decision/i],
    [403, null, /do not have permission/i],
    [409, 'something_new', /the backend’s words/],
  ] as const)('%i %s is said in words', (status, reason, words) => {
    expect(problem(refused(status, reason))).toMatch(words);
  });

  it('does not say a refused decision is a network error', () => {
    expect(problem(refused(409, 'approval_expired'))).not.toMatch(/could not reach/i);
    expect(problem({ kind: 'unreachable', reason: 'offline' })).toMatch(/could not reach/i);
  });
});
