import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApplicationDetail } from '@/pages/applications/ApplicationDetail';
import { ApplicationList } from '@/pages/applications/ApplicationList';

/**
 * The two application pages, which M1.5 shipped without component tests.
 *
 * Added by M1.7 and not for completeness: the changed-line coverage gate
 * (OPEN-22) failed on this milestone's own diff at 60 %, naming the load
 * expressions in these two files. Writing the gate and exempting the first
 * change it caught would be pointless, so these are the tests it asked for.
 *
 * The list's assertion is on the **request**, as in `ServerList.test.tsx`:
 * filtering is the backend's job inside a scope-filtered query, and a test
 * that only read the rendered rows would pass against a page that filtered
 * client-side.
 */

const APPLICATION = {
  id: 'app1',
  name: 'Checkout',
  slug: 'checkout',
  description: null,
  owner_team: 'payments',
  repository_reference: null,
  status: 'ACTIVE',
};

const DETAIL = {
  ...APPLICATION,
  health_check_config: { checks: [] },
  registered_service_count: 0,
  deployment_targets: { data_state: 'ok', source: 'postgresql', items: [] },
  servers: [
    {
      id: 's1',
      display_name: 'Web 01',
      hostname: 'web-01.internal.example',
      environment: 'staging',
      status: 'MANAGED',
    },
  ],
};

function mockPage(items: unknown[] = [APPLICATION]) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        items,
        page: { limit: 50, next_cursor: null, has_more: false, total: null },
        demo_mode: false,
      }),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function mockOne(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('ApplicationList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the applications it was given', async () => {
    mockPage();
    render(
      <MemoryRouter>
        <ApplicationList />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('link', { name: 'Checkout' })).toBeInTheDocument();
    expect(screen.getByText('checkout')).toBeInTheDocument();
    expect(screen.getByText('payments')).toBeInTheDocument();
  });

  it('sends the search term to the backend', async () => {
    const fetchMock = mockPage();
    render(
      <MemoryRouter>
        <ApplicationList />
      </MemoryRouter>,
    );
    await screen.findByRole('link', { name: 'Checkout' });
    await userEvent.type(screen.getByLabelText('Search'), 'check');

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('q=check'))).toBe(true);
    });
  });

  it('distinguishes an empty registry from a failure', async () => {
    mockPage([]);
    render(
      <MemoryRouter>
        <ApplicationList />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/No applications are registered/)).toBeInTheDocument();
  });

  it('reports a permission failure as such, not as an empty registry', async () => {
    mockOne(403, {});
    render(
      <MemoryRouter>
        <ApplicationList />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('do not have permission');
  });
});

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/applications/app1']}>
      <Routes>
        <Route path="/applications/:applicationId" element={<ApplicationDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ApplicationDetail', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the application and its servers, grouped by environment', async () => {
    mockOne(200, DETAIL);
    renderDetail();
    expect(await screen.findByRole('heading', { name: 'Checkout' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Web 01' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'staging', level: 3 })).toBeInTheDocument();
  });

  it('says a field was never recorded rather than showing a blank', async () => {
    mockOne(200, DETAIL);
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout' });
    // `null` is unknown and is never rendered as an empty cell or a zero
    // (PRD §4.2, CLAUDE.md §5). `description` and `repository_reference` are
    // both null in the fixture.
    expect(screen.getAllByText('Not recorded')).toHaveLength(2);
  });

  it('lists the registered pipelines, and says when approval is required', async () => {
    mockOne(200, {
      ...DETAIL,
      deployment_targets: {
        data_state: 'ok',
        source: 'postgresql',
        items: [
          {
            id: 't1',
            environment: 'production',
            pipeline_reference: 'team/checkout-deploy',
            allowed_ref_patterns: ['^main$'],
            requires_approval: true,
            rollback_supported: false,
            status: 'ACTIVE',
            updated_at: '2026-10-23T08:00:00Z',
          },
        ],
      },
    });
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout' });
    expect(screen.getByText('team/checkout-deploy')).toBeInTheDocument();
    expect(screen.getByText(/approval required/)).toBeInTheDocument();
  });

  it('says none are registered, or none the reader may see, which is not "not configured"', async () => {
    mockOne(200, DETAIL);
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout' });
    expect(screen.getByText(/or none that you may see/)).toBeInTheDocument();
    expect(screen.queryByText(/Not configured/)).toBeNull();
  });

  it('says it cannot tell when the reader holds no deployment permission', async () => {
    mockOne(200, {
      ...DETAIL,
      deployment_targets: {
        data_state: 'unavailable',
        source: 'postgresql',
        unavailable_reason: 'deployment_read_not_permitted',
        items: null,
      },
    });
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout' });
    expect(screen.getByText(/deployment_read_not_permitted/)).toBeInTheDocument();
  });

  it('reports a missing application rather than an empty page', async () => {
    mockOne(404, {});
    renderDetail();
    expect(await screen.findByRole('alert')).toHaveTextContent('Not found');
  });
});
