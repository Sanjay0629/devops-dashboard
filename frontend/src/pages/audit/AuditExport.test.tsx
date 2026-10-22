import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { AuditExport } from '@/pages/audit/AuditExport';

/**
 * §24.3 – §24.5 from the exporter's side.
 *
 * What the dialog owes: the rules before the click (once, bounded, expires), one export per
 * intent however many clicks, every state of the build in words, a download button that is
 * spent the moment it is used, and every refusal said as what it is.
 */

const ID = 'exp-1';

function status(over: Record<string, unknown> = {}) {
  return {
    export_id: ID,
    status: 'READY',
    from: '2026-10-21T09:00:00Z',
    to: '2026-10-22T09:00:00Z',
    row_count: 42,
    size_bytes: 4096,
    sha256: 'ab',
    failure_code: null,
    created_at: '2026-10-22T09:00:00Z',
    completed_at: '2026-10-22T09:00:03Z',
    expires_at: '2026-10-23T09:00:00Z',
    retrieved_at: null,
    ...over,
  };
}

function session(readOnly: boolean): SessionState {
  const value: Session = {
    user: { id: 'me', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'ADMINISTRATOR', scope: {} }],
    permissions: ['audit:read', 'admin:integrations'],
    session: { expires_at: '2026-10-23T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: readOnly,
  };
  return { kind: 'authenticated', session: value };
}

interface Api {
  request?: { status: number; body: unknown };
  statuses?: Record<string, unknown>[];
  download?: { status: number; body?: string };
}

function mockApi(api: Api = {}) {
  let polled = 0;
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const answer = api.request ?? {
        status: 202,
        body: { export_id: ID, status: 'QUEUED', expires_at: 'x', idempotent_replay: false },
      };
      return Promise.resolve({
        ok: answer.status < 400,
        status: answer.status,
        json: () => Promise.resolve(answer.body),
      });
    }
    if (url.endsWith('/download')) {
      const answer = api.download ?? { status: 200, body: '{"id":"e1"}\n' };
      return Promise.resolve({
        ok: answer.status < 400,
        status: answer.status,
        blob: () => Promise.resolve(new Blob([answer.body ?? ''])),
        json: () => Promise.resolve({}),
      });
    }
    const sequence = api.statuses ?? [status()];
    const next = sequence[Math.min(polled++, sequence.length - 1)];
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(next) });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const posts = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST');
const downloads = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call) => String(call[0]).endsWith('/download'));

function renderDialog(readOnly = false) {
  return render(
    <SessionContext.Provider
      value={{
        state: session(readOnly),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter>
        <AuditExport />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

async function open() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: /Export/ }));
  return user;
}

let saved: { href: string; download: string }[] = [];

beforeEach(() => {
  vi.unstubAllGlobals();
  saved = [];
  URL.createObjectURL = vi.fn(() => 'blob:fake');
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push({ href: this.href, download: this.download });
  });
});

describe('before the click', () => {
  it('states the rules: once, bounded, expiring, scoped, recorded', async () => {
    mockApi();
    renderDialog();
    await open();
    const rules = screen.getByRole('region', { name: 'Export the audit trail' });
    expect(rules).toHaveTextContent(/download it once/);
    expect(rules).toHaveTextContent(/31 days, 100 000 events and 64 MiB/);
    expect(rules).toHaveTextContent(/never cut short/);
    expect(rules).toHaveTextContent(/24 hours/);
    expect(rules).toHaveTextContent(/access scope/);
  });
});

describe('the lifecycle', () => {
  it('requests, tracks the build, and offers one download', async () => {
    const fetchMock = mockApi({ statuses: [status({ status: 'RUNNING' }), status()] });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    expect(await screen.findByText('Building…')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Download (once)' })).toBeInTheDocument();
    expect(screen.getByText(/Ready: 42 events, 4096 bytes/)).toBeInTheDocument();
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it('spends the download button the moment it is used, and saves one file', async () => {
    const fetchMock = mockApi();
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    const button = await screen.findByRole('button', { name: 'Download (once)' });
    await user.dblClick(button); // a double click is one download
    expect(await screen.findByRole('status')).toHaveTextContent(/deleted from the server/);
    expect(downloads(fetchMock)).toHaveLength(1);
    expect(saved).toEqual([{ href: 'blob:fake', download: `audit-export-${ID}.jsonl` }]);
    expect(screen.queryByRole('button', { name: 'Download (once)' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Request export' })).toBeDisabled();
  });

  it('says so when the file was already taken', async () => {
    mockApi({ download: { status: 409 } });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    await user.click(await screen.findByRole('button', { name: 'Download (once)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /already downloaded, or has expired/,
    );
  });

  it('keeps one idempotency key for the dialog, so a repeated click is one intent', async () => {
    const fetchMock = mockApi({
      request: { status: 409, body: { error: { code: 'export_in_progress', message: 'm' } } },
    });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    await waitFor(() => {
      expect(posts(fetchMock)).toHaveLength(2);
    });
    const keys = posts(fetchMock).map(
      (call) => new Headers((call[1] as RequestInit).headers).get('Idempotency-Key') ?? '',
    );
    expect(keys[0]).not.toBe('');
    expect(keys[0]).toBe(keys[1]);
  });
});

describe('refusals and failures, in words', () => {
  it.each([
    ['export_too_large', /never truncated|Nothing was truncated/],
    ['requester_not_authorised', /no longer allows/],
    ['internal_error', /Try again/],
  ])('a failed build (%s) says what it means', async (code, text) => {
    mockApi({ statuses: [status({ status: 'FAILED', failure_code: code })] });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
    expect(screen.queryByRole('button', { name: 'Download (once)' })).toBeNull();
  });

  it('says an export is already in progress', async () => {
    mockApi({
      request: { status: 409, body: { error: { code: 'export_in_progress', message: 'm' } } },
    });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /already have an export in progress/,
    );
  });

  it("shows the backend's reason for a window it refuses", async () => {
    mockApi({
      request: {
        status: 422,
        body: {
          error: {
            code: 'validation_failed',
            message: 'The request contains invalid fields.',
            details: { fields: [{ message: 'An export is limited to 31 days.' }] },
          },
        },
      },
    });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/limited to 31 days/);
  });

  it('says a refusal for lack of permission is a refusal', async () => {
    mockApi({ request: { status: 403, body: { error: { code: 'forbidden', message: 'no' } } } });
    renderDialog();
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Request export' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/not allowed to export/);
  });

  it('cannot be requested in read-only mode', async () => {
    const fetchMock = mockApi();
    renderDialog(true);
    await open();
    expect(screen.getByRole('button', { name: 'Request export' })).toBeDisabled();
    expect(posts(fetchMock)).toHaveLength(0);
  });

  it('does not poll once the export is finished', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = mockApi();
      renderDialog();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.click(screen.getByRole('button', { name: /Export/ }));
      await user.click(screen.getByRole('button', { name: 'Request export' }));
      await screen.findByRole('button', { name: 'Download (once)' });
      const before = fetchMock.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(fetchMock.mock.calls.length).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('accessibility', () => {
  it('has no axe violations in the open dialog', async () => {
    mockApi();
    const { container } = renderDialog();
    await open();
    const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
