import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { AuditLog } from '@/pages/audit/AuditLog';
import source from '@/pages/audit/AuditLog.tsx?raw';

/**
 * §24.1 from the reader's side (PRD §4.12).
 *
 * The page's duties: it asks once per search and once per page (reading the trail is itself
 * audited), never on a timer; event content is text, whatever it contains; an empty answer does
 * not claim nothing happened; a malformed action never reaches the backend; and the export is only
 * offered to a person the advisory permissions say may use it.
 */

function event(over: Record<string, unknown> = {}) {
  return {
    id: 'e1',
    occurred_at: '2026-10-22T09:00:00Z',
    actor_type: 'USER',
    actor_id: 'u1',
    actor_label: 'riley@example.invalid',
    action_type: 'ROLE_GRANTED',
    target_type: 'user',
    target_id: 'u9',
    environment: null,
    correlation_id: 'corr-1',
    approval_id: null,
    outcome: 'SUCCESS',
    source_ip: '10.0.0.7',
    user_agent: 'Mozilla/5.0',
    sanitized_metadata: { role: 'VIEWER' },
    ...over,
  };
}

function list(items: unknown[], page: Record<string, unknown> = {}) {
  return { items, page: { limit: 50, next_cursor: null, has_more: false, total: null, ...page } };
}

function state(permissions: string[]): SessionState {
  const session: Session = {
    user: { id: 'me', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'ADMINISTRATOR', scope: {} }],
    permissions,
    session: { expires_at: '2026-10-23T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: false,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function mockApi(respond: (url: string) => Promise<unknown> = () => reply(200, list([event()]))) {
  const fetchMock = vi.fn().mockImplementation((url: string) => respond(url));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
const urls = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.map((c) => String(c[0]));

function renderPage(permissions = ['audit:read'], path = '/audit') {
  return render(
    <SessionContext.Provider
      value={{
        state: state(permissions),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter initialEntries={[path]}>
        <AuditLog />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('reading the trail is itself recorded, so the page is sparing', () => {
  it('asks once to load, and does not ask again on its own', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = mockApi();
      renderPage();
      await screen.findByText('ROLE_GRANTED');
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000); // ten minutes of "watching"
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(1000);
      expect(urls(fetchMock)).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows the detail from the list it already holds, without asking again', async () => {
    const fetchMock = mockApi();
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Details of ROLE_GRANTED/ }));
    const panel = screen.getByRole('complementary', { name: 'Event detail' });
    expect(within(panel).getByText('corr-1')).toBeInTheDocument();
    expect(within(panel).getByText('10.0.0.7')).toBeInTheDocument();
    expect(urls(fetchMock)).toHaveLength(1);
  });

  it('asks again only for a search or a page turned', async () => {
    const fetchMock = mockApi((url) =>
      url.includes('cursor=n1')
        ? reply(200, list([event({ id: 'e2', action_type: 'ROLE_REVOKED' })]))
        : reply(200, list([event()], { next_cursor: 'n1', has_more: true })),
    );
    renderPage();
    await screen.findByText('ROLE_GRANTED');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('ROLE_REVOKED')).toBeInTheDocument();
    expect(urls(fetchMock)).toHaveLength(2);
  });
});

describe('event content is text', () => {
  it('displays markup in metadata and labels without interpreting it', async () => {
    mockApi(() =>
      reply(
        200,
        list([
          event({
            actor_label: '<b>boss</b>',
            sanitized_metadata: { note: '<script>alert(1)</script><img src=x onerror=alert(2)>' },
          }),
        ]),
      ),
    );
    const { container } = renderPage();
    const user = userEvent.setup();
    expect(await screen.findByText('<b>boss</b>')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Details of/ }));
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(container.querySelector('script, img, b')).toBeNull();
    expect(source).not.toContain('dangerouslySetInnerHTML');
  });
});

describe('states', () => {
  it('does not claim "nothing happened", and says why sign-ins may be missing', async () => {
    mockApi(() => reply(200, list([])));
    renderPage();
    const note = await screen.findByText(/none are within your access scope/);
    expect(note).toHaveTextContent(/no environment, such as sign-ins/);
  });

  it('shows a refusal in words, not as an empty table', async () => {
    mockApi(() => reply(403, { error: { code: 'forbidden', message: 'no' } }));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have permission/);
  });

  it('shows a transport failure with a retry', async () => {
    mockApi(() => Promise.reject(new Error('network down')));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not reach the backend/);
  });
});

describe('filters', () => {
  it('sends what was chosen, only after Search', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByText('ROLE_GRANTED');
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Action'), 'AUTHZ_DENIED');
    await user.selectOptions(screen.getByLabelText('Outcome'), 'DENIED');
    expect(urls(fetchMock)).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toContain('action_type=AUTHZ_DENIED');
    });
    expect(urls(fetchMock).at(-1)).toContain('outcome=DENIED');
  });

  it('never sends an action that is not a plain token, and says why', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByText('ROLE_GRANTED');
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Action'), 'role%');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/capital letters and underscores/);
    expect(urls(fetchMock)).toHaveLength(1);
  });

  it('takes ids from the address, and "everything with this correlation id" starts a fresh search on it', async () => {
    const fetchMock = mockApi();
    renderPage(['audit:read'], '/audit?actor_id=u1');
    await screen.findByText('ROLE_GRANTED');
    expect(urls(fetchMock)[0]).toContain('actor_id=u1');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Details of/ }));
    await user.click(screen.getByRole('button', { name: /this correlation id/ }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toContain('correlation_id=corr-1');
    });
    expect(urls(fetchMock).at(-1)).not.toContain('actor_id');
  });
});

describe('the export is offered only where the advisory permissions allow it', () => {
  it('is absent without admin:integrations', async () => {
    mockApi();
    renderPage(['audit:read']);
    await screen.findByText('ROLE_GRANTED');
    expect(screen.queryByRole('button', { name: /Export/ })).toBeNull();
  });

  it('is offered with it', async () => {
    mockApi();
    renderPage(['audit:read', 'admin:integrations']);
    expect(await screen.findByRole('button', { name: /Export/ })).toBeInTheDocument();
  });
});

describe('accessibility', () => {
  it('has no axe violations with the detail panel open', async () => {
    mockApi();
    const { container } = renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Details of/ }));
    const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
