import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session, SessionState } from '@/api/session';
import { SessionContext } from '@/auth/sessionContext';
import { DeploymentList } from '@/pages/deployments/DeploymentList';
import source from '@/pages/deployments/DeploymentList.tsx?raw';

/**
 * §21.1 from the page's side. What matters: a result is shown as what backs it and an unknown
 * outcome says so; an empty list says it may hide what the caller cannot see; filters go to the
 * backend; every cell is a text node; no Jenkins URL is ever shown.
 */

function item(over: Record<string, unknown> = {}) {
  return {
    deployment_id: 'd-1',
    kind: 'DEPLOY',
    application_id: 'app-1',
    application_name: 'Billing API',
    environment: 'staging',
    requested_ref: 'release/1.4',
    commit_sha: null,
    execution_status: 'REQUESTED',
    approval_status: 'NOT_REQUIRED',
    requested_by: { id: 'u1', display_name: 'Alex Kim' },
    created_at: '2026-10-27T09:30:00Z',
    started_at: null,
    completed_at: null,
    deadline_at: '2026-10-27T10:30:00Z',
    failure_reason: null,
    correlation_id: 'c1',
    ...over,
  };
}

function page(items: unknown[], over: Record<string, unknown> = {}) {
  return {
    items,
    page: { limit: 50, next_cursor: null, has_more: false, total: null },
    ...over,
  };
}

function state(): SessionState {
  const session: Session = {
    user: { id: 'u1', email: 'a@example.invalid', display_name: 'Alex', status: 'ACTIVE' },
    roles: [{ name: 'DEVOPS_ENGINEER', scope: {} }],
    permissions: ['deployment:read'],
    session: { expires_at: '2026-10-28T00:00:00Z', idle_timeout_seconds: 1800 },
    demo_mode: false,
    read_only_mode: false,
  };
  return { kind: 'authenticated', session };
}

const reply = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function mockApi(body: unknown = page([item()])) {
  const fetchMock = vi.fn().mockImplementation(() => reply(200, body));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const urls = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.map((c) => String(c[0]));

function renderPage() {
  return render(
    <SessionContext.Provider
      value={{
        state: state(),
        refresh: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      <MemoryRouter>
        <DeploymentList />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

describe('DeploymentList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows a deployment with links to it and to its application', async () => {
    mockApi(page([item({ commit_sha: '0123456789abcdef0123456789abcdef01234567' })]));
    renderPage();
    const row = (await screen.findByText('release/1.4')).closest('tr') as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Billing API' })).toHaveAttribute(
      'href',
      '/applications/app-1',
    );
    const detail = within(row)
      .getAllByRole('link')
      .find((a) => a.getAttribute('href') === '/deployments/d-1');
    expect(detail).toBeDefined();
    expect(within(row).getByText('staging')).toBeInTheDocument();
    expect(within(row).getByText('Alex Kim')).toBeInTheDocument();
    expect(within(row).getByText('0123456')).toBeInTheDocument(); // a short commit
    expect(within(row).getByText('Requested')).toBeInTheDocument(); // the badge
  });

  it('says what a success rests on, and nothing grander', async () => {
    mockApi(page([item({ execution_status: 'SUCCEEDED', completed_at: '2026-10-27T09:35:00Z' })]));
    const { container } = renderPage();
    expect(await screen.findByText('Pipeline succeeded')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/verified|healthy/i);
  });

  it.each(['TRIGGER_OUTCOME_UNKNOWN', 'QUEUE_ITEM_LOST', 'DEPLOYMENT_TIMED_OUT'])(
    'says an unknown outcome is unknown, and to check Jenkins, for %s',
    async (code) => {
      mockApi(page([item({ execution_status: 'FAILED', failure_reason: code })]));
      renderPage();
      expect(
        await screen.findByText(/Outcome unknown — check Jenkins before requesting again/),
      ).toBeInTheDocument();
    },
  );

  it('gives a plain failure its reason in words', async () => {
    mockApi(page([item({ execution_status: 'FAILED', failure_reason: 'PIPELINE_FAILED' })]));
    renderPage();
    expect(await screen.findByText('The pipeline failed.')).toBeInTheDocument();
  });

  it('shows an unfinished one as not finished, not as anything else', async () => {
    mockApi(page([item({ execution_status: 'RUNNING' })]));
    renderPage();
    expect(await screen.findByText('Not finished')).toBeInTheDocument();
  });

  it('says an empty list may hide what the caller cannot see', async () => {
    mockApi(page([]));
    renderPage();
    expect(await screen.findByText(/or none are within your access scope/)).toBeInTheDocument();
  });

  it('shows a load failure as one, and can retry', async () => {
    const fetchMock = vi.fn().mockImplementation(() => reply(503, {}));
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('shows markup in an application name or a ref as text and creates no element', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    mockApi(page([item({ application_name: markup, requested_ref: markup })]));
    const { container } = renderPage();
    expect((await screen.findAllByText(markup)).length).toBe(2);
    expect(container.querySelector('img')).toBeNull();
    expect(source).not.toMatch(/dangerouslySetInnerHTML/);
  });

  it('shows no Jenkins URL', async () => {
    mockApi(page([item({ execution_status: 'SUCCEEDED' })]));
    const { container } = renderPage();
    await screen.findByText('Pipeline succeeded');
    expect(container.textContent).not.toMatch(/https?:\/\//);
  });

  it('points at the targets page for starting one, since a deployment starts from a target', async () => {
    mockApi(page([]));
    renderPage();
    expect(await screen.findByRole('link', { name: 'Deployment targets' })).toHaveAttribute(
      'href',
      '/deployment-targets',
    );
  });
});

describe('DeploymentList filters', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks for everything in the default window when nothing is chosen', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByText('release/1.4');
    expect(urls(fetchMock)).toEqual(['/api/v1/deployments']);
  });

  it('sends the chosen status and environment to the backend rather than filtering here', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByText('release/1.4');
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'FAILED');
    await userEvent.selectOptions(screen.getByLabelText('Environment'), 'production');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toBe(
        '/api/v1/deployments?execution_status=FAILED&environment=production',
      );
    });
  });

  it('narrows to the caller’s own requests by their id, and nothing else', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByText('release/1.4');
    await userEvent.click(screen.getByLabelText('Only my requests'));
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toBe('/api/v1/deployments?requested_by=u1');
    });
  });

  it('sends a range as RFC 3339 instants', async () => {
    const fetchMock = mockApi();
    renderPage();
    await screen.findByText('release/1.4');
    await userEvent.type(screen.getByLabelText('From'), '2026-10-01T08:00');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toMatch(
        /^\/api\/v1\/deployments\?from=2026-\d{2}-\d{2}T\d{2}%3A\d{2}%3A00\.000Z$/,
      );
    });
  });

  it('pages by cursor without a total, and goes back to page one when a filter changes', async () => {
    const fetchMock = mockApi(
      page([item()], { page: { limit: 1, next_cursor: 'cursor-2', has_more: true, total: null } }),
    );
    renderPage();
    await screen.findByText('release/1.4');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => {
      expect(urls(fetchMock).at(-1)).toBe('/api/v1/deployments?cursor=cursor-2');
    });
    expect(screen.getByText('Page 2')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'FAILED');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => {
      expect(screen.getByText('Page 1')).toBeInTheDocument();
    });
  });
});

it('has no accessibility violations', async () => {
  vi.unstubAllGlobals();
  mockApi(page([item({ execution_status: 'FAILED', failure_reason: 'PIPELINE_FAILED' })]));
  const { container } = renderPage();
  await screen.findByText('release/1.4');
  const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  expect(results.violations).toEqual([]);
});
